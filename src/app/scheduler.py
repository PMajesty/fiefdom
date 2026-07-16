"""Фоновый планировщик: дневной тик и катастрофы."""
from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from app import balance as B
from app.config import TIMEZONE
from app.domain.events import (
    CATASTROPHES,
    next_catastrophe_delay_days,
    pick_catastrophe,
)
from app.handlers.shared import get_engine, send_game

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
    local_date = local_now.date()
    tick_h = int(realm.get("tick_hour") if realm.get("tick_hour") is not None else 13)
    tick_m = int(realm.get("tick_minute") if realm.get("tick_minute") is not None else 0)

    last_tick_date = _as_date(realm.get("last_tick_local_date"))
    due_tick = (last_tick_date is None or local_date > last_tick_date) and (
        local_now.hour > tick_h or (local_now.hour == tick_h and local_now.minute >= tick_m)
    )
    if due_tick:
        result = engine.run_realm_tick(realm_id)
        digest = result.get("digest")
        chat_id = result.get("chat_id") or realm.get("chat_id")
        if digest and chat_id:
            await send_game(bot, chat_id, digest)
        logger.info("Tick ran for realm %s day digest posted", realm_id)
        realm = engine.db.get_realm(realm_id) or realm

    await _maybe_post_catastrophe(bot, engine, realm, local_now)
    await _resolve_expired_catastrophes(bot, engine, realm)


async def _maybe_post_catastrophe(bot: Bot, engine, realm: dict, local_now: datetime) -> None:
    next_at = realm.get("next_catastrophe_at")
    if not next_at:
        return
    if next_at.tzinfo is None:
        next_at = next_at.replace(tzinfo=timezone.utc)
    if _utcnow() < next_at:
        return

    hour = local_now.hour
    if hour < B.CATASTROPHE_POST_HOUR_START or hour >= B.CATASTROPHE_POST_HOUR_END:
        return

    # уже есть активная катастрофа — не дублируем
    active = engine.db.get_active_events(realm["id"], kind="catastrophe")
    if active:
        return

    rng = random.Random()
    key = pick_catastrophe(rng, realm.get("last_catastrophe_key"))
    meta = CATASTROPHES[key]
    window_h = rng.randint(B.CATASTROPHE_WINDOW_HOURS_MIN, B.CATASTROPHE_WINDOW_HOURS_MAX)
    resolves_at = _utcnow() + timedelta(hours=window_h)
    narrative = meta["canned_narrative"]
    event = engine.db.create_event(
        realm_id=realm["id"],
        kind="catastrophe",
        event_key=key,
        payload={"threshold_hint": True},
        narrative=narrative,
        status="active",
        resolves_at=resolves_at,
    )

    delay = next_catastrophe_delay_days(rng)
    engine.db.update_realm(
        realm["id"],
        last_catastrophe_key=key,
        next_catastrophe_at=_utcnow() + timedelta(days=delay),
    )

    players = max(1, len(engine.db.list_fiefs(realm["id"])))
    extra = ""
    if key == "bandit_night":
        need = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
        extra = f"\nНужно собрать ≥ {need} силы. Вклад: кнопка ниже (−5 силы за нажатие)."

    text = (
        f"⚠️ <b>{meta['name_ru']}</b>\n"
        f"{narrative}{extra}\n"
        f"Окно до {resolves_at.astimezone(local_now.tzinfo).strftime('%d.%m %H:%M')}."
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
    now = _utcnow()
    events = engine.db.get_active_events(realm["id"], kind="catastrophe")
    for ev in events:
        resolves_at = ev.get("resolves_at")
        if not resolves_at:
            continue
        if resolves_at.tzinfo is None:
            resolves_at = resolves_at.replace(tzinfo=timezone.utc)
        if resolves_at > now:
            continue

        key = ev.get("event_key")
        if key == "bandit_night":
            result_text = _resolve_bandit_night(engine, realm, ev)
        else:
            engine.db.update_event(ev["id"], status="resolved")
            meta = CATASTROPHES.get(key) or {}
            name = meta.get("name_ru", key)
            result_text = f"Катастрофа «{name}» завершилась."

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

    # провал: потери зерна у не-вкладчиков
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
    who = ", ".join(loss_note[:8]) if loss_note else "—"
    return (
        f"☠️ Ночь бандитов: провал ({total_might}/{threshold} силы). "
        f"Пострадали: {who}."
    )
