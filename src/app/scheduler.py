"""Фоновый планировщик: дневной тик и катастрофы."""
from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from app import balance as B
from app.config import TIMEZONE, tick_slots
from app.domain.events import (
    CATASTROPHES,
    MINOR_EVENTS,
    next_catastrophe_delay_ticks,
    pick_catastrophe,
)
from app.domain.tick_schedule import due_tick_slot
from app.handlers.shared import get_engine, post_digest, send_game

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None


async def scheduler_loop(bot: Bot, stop_event: asyncio.Event | None = None) -> None:
    logger.info("Scheduler started (every %ss)", POLL_SECONDS)
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            await _scheduler_tick(bot)
        except Exception:
            logger.exception("scheduler tick failed")
        try:
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            break
    logger.info("Scheduler stopped")


async def _scheduler_tick(bot: Bot) -> None:
    engine = get_engine()
    realms = engine.db.list_realms()
    for realm in realms:
        try:
            await _process_realm(bot, engine, realm)
        except Exception:
            logger.exception("realm %s scheduler error", realm.get("id"))


async def _process_realm(bot: Bot, engine, realm: dict) -> None:
    realm_id = realm["id"]
    # свежие данные
    realm = engine.db.get_realm(realm_id) or realm
    tz_name = realm.get("timezone") or TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(TIMEZONE)

    local_now = datetime.now(tz)
    last_tick_date = _as_date(realm.get("last_tick_local_date"))
    last_slot = realm.get("last_tick_slot")
    slot_index = due_tick_slot(
        local_now=local_now,
        last_tick_local_date=last_tick_date,
        last_tick_slot=int(last_slot) if last_slot is not None else None,
        slots=tick_slots(),
    )
    if slot_index is not None:
        result = engine.run_realm_tick(realm_id, tick_slot=slot_index)
        digest = result.get("digest")
        chat_id = result.get("chat_id") or realm.get("chat_id")
        if digest and chat_id:
            await post_digest(bot, chat_id, realm_id, digest)
        deserter_event = result.get("deserter_event")
        if deserter_event and chat_id:
            await post_deserter_race(bot, chat_id, deserter_event)
        logger.info(
            "Tick ran for realm %s slot %s digest posted", realm_id, slot_index
        )
        realm = engine.db.get_realm(realm_id) or realm

    await _maybe_post_catastrophe(bot, engine, realm)
    await _resolve_expired_catastrophes(bot, engine, realm)


async def post_deserter_race(bot: Bot, chat_id: int, event: dict) -> None:
    meta = MINOR_EVENTS["deserter"]
    label = (meta.get("button_labels") or ["Взять в дружину"])[0]
    narrative = event.get("narrative") or meta["canned_narrative"]
    text = f"⚔️ <b>{meta['name_ru']}</b>\n{narrative}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"des:{event['id']}",
                )
            ]
        ]
    )
    await send_game(bot, chat_id, text, reply_markup=kb)


async def _maybe_post_catastrophe(bot: Bot, engine, realm: dict) -> None:
    tick_index = int(realm.get("tick_index") or 0)
    next_tick = realm.get("next_catastrophe_tick")
    if next_tick is None:
        return
    if tick_index < int(next_tick):
        return

    # уже есть активная катастрофа - не дублируем
    active = engine.db.get_active_events(realm["id"], kind="catastrophe")
    if active:
        return

    rng = random.Random()
    key = pick_catastrophe(rng, realm.get("last_catastrophe_key"))
    meta = CATASTROPHES[key]
    window_t = rng.randint(B.CATASTROPHE_WINDOW_TICKS_MIN, B.CATASTROPHE_WINDOW_TICKS_MAX)
    resolves_tick = tick_index + window_t
    narrative = meta["canned_narrative"]
    event = engine.db.create_event(
        realm_id=realm["id"],
        kind="catastrophe",
        event_key=key,
        payload={"threshold_hint": True},
        narrative=narrative,
        status="active",
        resolves_tick=resolves_tick,
    )

    delay = next_catastrophe_delay_ticks(rng)
    engine.db.update_realm(
        realm["id"],
        last_catastrophe_key=key,
        next_catastrophe_tick=tick_index + delay,
        next_catastrophe_at=None,
    )

    players = max(1, len(engine.db.list_fiefs(realm["id"])))
    extra = ""
    if key == "bandit_night":
        need = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
        extra = f"\nНужно собрать ≥ {need} силы. Вклад: кнопка ниже (−5 силы за нажатие)."

    text = (
        f"⚠️ <b>{meta['name_ru']}</b>\n"
        f"{narrative}{extra}\n"
        f"Окно: {window_t} тик(а)."
    )
    chat_id = realm.get("chat_id")
    if chat_id:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        kb = None
        if key == "bandit_night":
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Вложить 5 силы",
                            callback_data=f"cat:{event['id']}:might5",
                        )
                    ]
                ]
            )
        await send_game(bot, chat_id, text, reply_markup=kb)


async def _resolve_expired_catastrophes(bot: Bot, engine, realm: dict) -> None:
    tick_index = int(realm.get("tick_index") or 0)
    events = engine.db.get_active_events(realm["id"], kind="catastrophe")
    for ev in events:
        resolves_tick = ev.get("resolves_tick")
        if resolves_tick is None:
            continue
        if int(resolves_tick) > tick_index:
            continue

        key = ev.get("event_key")
        if key == "bandit_night":
            result_text = _resolve_bandit_night(engine, realm, ev)
        else:
            engine.db.update_event(ev["id"], status="resolved")
            meta = CATASTROPHES.get(key) or {}
            name = meta.get("name_ru", key)
            result_text = f"Катастрофа \"{name}\" завершилась."

        chat_id = realm.get("chat_id")
        if chat_id and result_text:
            await send_game(bot, chat_id, result_text)


def _resolve_bandit_night(engine, realm: dict, event: dict) -> str:
    fiefs = engine.db.list_fiefs(realm["id"])
    players = max(1, len(fiefs))
    threshold = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
    actions = engine.db.event_actions(event["id"])
    total_might = sum(int(a.get("amount") or 0) for a in actions)
    contributors = {int(a["fief_id"]) for a in actions if int(a.get("amount") or 0) > 0}

    if total_might >= threshold:
        engine.db.update_event(event["id"], status="resolved")
        loot_each = B.BANDIT_NIGHT_LOOT_PER_PLAYER
        if contributors:
            share = max(1, int((loot_each * players) // len(contributors)))
            for fid in contributors:
                f = engine.db.get_fief(fid)
                if f:
                    engine.db.update_fief(fid, goods=f["goods"] + share)
        return (
            f"⚔️ Ночь бандитов отбита! Собрано {total_might}/{threshold} силы. "
            f"Участники получили добычу."
        )

    loss_note = []
    for f in fiefs:
        if f["id"] in contributors:
            continue
        if f.get("frozen"):
            continue
        loss = max(1, int(f["grain"] * 0.15))
        engine.db.update_fief(f["id"], grain=max(0, f["grain"] - loss))
        loss_note.append(f["name"])

    engine.db.update_event(event["id"], status="resolved")
    who = ", ".join(loss_note[:8]) if loss_note else "-"
    return (
        f"☠️ Ночь бандитов: провал ({total_might}/{threshold} силы). "
        f"Пострадали: {who}."
    )
