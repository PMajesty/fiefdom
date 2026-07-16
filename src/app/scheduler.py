"""Фоновый планировщик: дневной тик континента и катастрофы."""
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
    world = engine.db.get_or_create_world()
    tz_name = world.get("timezone") or TIMEZONE
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(TIMEZONE)

    local_now = datetime.now(tz)
    slot_index = due_tick_slot(
        local_now=local_now,
        last_tick_local_date=_as_date(world.get("last_tick_local_date")),
        last_tick_slot=(
            int(world["last_tick_slot"]) if world.get("last_tick_slot") is not None else None
        ),
        slots=tick_slots(),
    )
    incomplete = engine.world_tick_incomplete(int(world["id"]))
    if incomplete or slot_index is not None:
        # incomplete: догоняем долины без нового слота; иначе обычный due.
        result = engine.run_world_tick(
            int(world["id"]),
            tick_slot=None if incomplete else slot_index,
        )
        for item in result.get("realms") or []:
            if item.get("skipped"):
                continue
            digest = item.get("digest")
            chat_id = item.get("chat_id")
            realm_id = item.get("realm_id")
            if digest and chat_id and realm_id:
                await post_digest(bot, chat_id, int(realm_id), digest)
            deserter_event = item.get("deserter_event")
            if deserter_event and chat_id:
                await post_deserter_race(bot, chat_id, deserter_event)
        logger.info(
            "World tick slot %s posted for %s realms (resumed=%s incomplete=%s)",
            slot_index,
            len([x for x in (result.get("realms") or []) if not x.get("skipped")]),
            result.get("resumed"),
            result.get("incomplete"),
        )
        world = engine.db.get_world(int(world["id"])) or world

    try:
        await _maybe_post_world_catastrophe(bot, engine, world)
    except Exception:
        logger.exception("world catastrophe post failed")

    for realm in engine.db.list_realms_by_chain(int(world["id"])):
        try:
            await _resolve_expired_catastrophes(bot, engine, realm)
        except Exception:
            logger.exception("realm %s catastrophe resolve error", realm.get("id"))


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


def _active_catastrophe(engine, realm_id: int) -> dict | None:
    events = engine.db.get_active_events(realm_id, kind="catastrophe")
    return events[0] if events else None


async def _post_catastrophe_message(
    bot: Bot, engine, realm: dict, event: dict, key: str, narrative: str, window_t: int
) -> None:
    meta = CATASTROPHES[key]
    players = max(1, len(engine.db.list_fiefs(realm["id"])))
    extra = ""
    if key == "bandit_night":
        need = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
        extra = (
            f"\nНужно собрать ≥ {need} силы. "
            f"Вклад: кнопка ниже (−5 силы за нажатие)."
        )
    elif key == "cattle_plague":
        extra = (
            "\nПоля без тягла дают половину. В личке: "
            "\"Забить скот\" (−20 зерна) - снять мор у своей усадьбы."
        )
    text = (
        f"⚠️ <b>{meta['name_ru']}</b>\n"
        f"{narrative}{extra}\n"
        f"Окно: {window_t} тик(а)."
    )
    chat_id = realm.get("chat_id")
    if not chat_id:
        return
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


def _advance_catastrophe_schedule(engine, world: dict, key: str, tick_index: int) -> None:
    rng = random.Random()
    delay = next_catastrophe_delay_ticks(rng)
    next_key = pick_catastrophe(rng, key)
    engine.db.update_world(
        int(world["id"]),
        last_catastrophe_key=key,
        next_catastrophe_tick=tick_index + delay,
        next_catastrophe_key=next_key,
        next_catastrophe_at=None,
    )
    engine.db.sync_realms_clock_from_world(int(world["id"]))


async def _maybe_post_world_catastrophe(bot: Bot, engine, world: dict) -> None:
    """Глобальная катастрофа: сначала все события в БД, потом сдвиг расписания, потом посты.

    Неполный fan-out дополняется на следующем опросе без второго цикла бед.
    """
    tick_index = int(world.get("tick_index") or 0)
    next_tick = world.get("next_catastrophe_tick")
    realms = engine.db.list_realms_by_chain(int(world["id"]))
    if not realms:
        return

    active_pairs: list[tuple[dict, dict]] = []
    for realm in realms:
        ev = _active_catastrophe(engine, int(realm["id"]))
        if ev:
            active_pairs.append((realm, ev))

    if active_pairs:
        wave_keys = {
            (str(ev.get("event_key")), int(ev.get("resolves_tick") or 0))
            for _r, ev in active_pairs
        }
        if len(wave_keys) != 1:
            return
        key, resolves_tick = next(iter(wave_keys))
        have_ids = {int(r["id"]) for r, _ev in active_pairs}
        meta = CATASTROPHES.get(key) or {}
        narrative = meta.get("canned_narrative") or ""
        window_t = max(1, resolves_tick - tick_index) if resolves_tick >= tick_index else 1
        for realm in realms:
            if int(realm["id"]) in have_ids:
                continue
            payload: dict = {"threshold_hint": True}
            if key == "cattle_plague":
                payload = {"mitigated_fief_ids": []}
            event = engine.db.create_event(
                realm_id=realm["id"],
                kind="catastrophe",
                event_key=key,
                payload=payload,
                narrative=narrative,
                status="active",
                resolves_tick=resolves_tick,
            )
            try:
                await _post_catastrophe_message(
                    bot, engine, realm, event, key, narrative, window_t
                )
            except Exception:
                logger.exception(
                    "catastrophe resume send failed realm=%s", realm.get("id")
                )
        # Если волна началась, а расписание ещё не сдвинули (упали до advance).
        if next_tick is not None and tick_index >= int(next_tick):
            _advance_catastrophe_schedule(engine, world, key, tick_index)
        return

    if next_tick is None or tick_index < int(next_tick):
        return

    rng = random.Random()
    key = world.get("next_catastrophe_key") or pick_catastrophe(
        rng, world.get("last_catastrophe_key")
    )
    meta = CATASTROPHES[key]
    window_t = rng.randint(B.CATASTROPHE_WINDOW_TICKS_MIN, B.CATASTROPHE_WINDOW_TICKS_MAX)
    resolves_tick = tick_index + window_t
    narrative = meta["canned_narrative"]

    created: list[tuple[dict, dict]] = []
    for realm in realms:
        payload: dict = {"threshold_hint": True}
        if key == "cattle_plague":
            payload = {"mitigated_fief_ids": []}
        event = engine.db.create_event(
            realm_id=realm["id"],
            kind="catastrophe",
            event_key=key,
            payload=payload,
            narrative=narrative,
            status="active",
            resolves_tick=resolves_tick,
        )
        created.append((realm, event))

    # Сдвиг до Telegram-постов: повторный due не откроет вторую волну.
    _advance_catastrophe_schedule(engine, world, key, tick_index)

    for realm, event in created:
        try:
            await _post_catastrophe_message(
                bot, engine, realm, event, key, narrative, window_t
            )
        except Exception:
            logger.exception("catastrophe send failed realm=%s", realm.get("id"))


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
        elif key == "cattle_plague":
            engine.db.update_event(ev["id"], status="resolved")
            result_text = "Мор скота отступил. Поля снова дышат."
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
        loss = max(1, int(f["grain"] * B.BANDIT_NIGHT_FAIL_GRAIN_FRAC))
        engine.db.update_fief(f["id"], grain=max(0, f["grain"] - loss))
        loss_note.append(f["name"])

    engine.db.update_event(event["id"], status="resolved")
    who = ", ".join(loss_note[:8]) if loss_note else "-"
    return (
        f"☠️ Ночь бандитов: провал ({total_might}/{threshold} силы). "
        f"Пострадали: {who}."
    )
