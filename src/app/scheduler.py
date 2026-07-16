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
    next_catastrophe_delay_ticks,
    pick_catastrophe,
)
from app.domain.tick_schedule import due_tick_slot
from app.handlers.shared import announce_realm, get_engine, post_digest

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
    await announce_realm(bot, int(realm["id"]), text, reply_markup=kb)


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


def _wave_pair(event: dict) -> tuple[str, int]:
    return (str(event.get("event_key")), int(event.get("resolves_tick") or 0))


def _pick_canonical_catastrophe_wave(
    active_pairs: list[tuple[dict, dict]], world: dict
) -> tuple[str, int]:
    """Каноническая волна при расхождении активных катастроф между долинами.

    Порядок: большинство; ключ по состоянию расписания; earliest resolves_tick;
    стабильный event_key.
    """
    counts: dict[tuple[str, int], int] = {}
    for _realm, ev in active_pairs:
        pair = _wave_pair(ev)
        counts[pair] = counts.get(pair, 0) + 1
    max_count = max(counts.values())
    candidates = [pair for pair, n in counts.items() if n == max_count]
    if len(candidates) == 1:
        return candidates[0]

    tick_index = int(world.get("tick_index") or 0)
    next_tick = world.get("next_catastrophe_tick")
    schedule_due = next_tick is not None and tick_index >= int(next_tick)
    # Due: волна ещё из next_*; уже сдвинули - из last_* (текущая/прошлая волна).
    preferred_key = (
        world.get("next_catastrophe_key")
        if schedule_due
        else world.get("last_catastrophe_key")
    )
    if preferred_key is not None:
        keyed = [p for p in candidates if p[0] == str(preferred_key)]
        if keyed:
            candidates = keyed
            if len(candidates) == 1:
                return candidates[0]

    min_resolves = min(p[1] for p in candidates)
    candidates = [p for p in candidates if p[1] == min_resolves]
    if len(candidates) == 1:
        return candidates[0]

    candidates.sort(key=lambda p: p[0])
    return candidates[0]


def _heal_divergent_catastrophe_wave(
    engine, world: dict, active_pairs: list[tuple[dict, dict]]
) -> tuple[str, int, set[int]]:
    """Сводит расходящиеся активные волны к одной; без игровых штрафов/лута.

    Возвращает (event_key, resolves_tick, realm_ids с канонической волной).
    """
    key, resolves_tick = _pick_canonical_catastrophe_wave(active_pairs, world)
    canonical = (key, resolves_tick)
    have_ids: set[int] = set()
    closed: list[int] = []
    for realm, ev in active_pairs:
        rid = int(realm["id"])
        if _wave_pair(ev) == canonical:
            have_ids.add(rid)
            continue
        # Sync heal: закрываем без _resolve_bandit_night и прочих gameplay-эффектов.
        engine.db.update_event(int(ev["id"]), status="resolved")
        closed.append(rid)
    logger.warning(
        "catastrophe wave divergence healed world=%s canonical=%s resolves_tick=%s "
        "kept_realms=%s closed_realms=%s",
        world.get("id"),
        key,
        resolves_tick,
        sorted(have_ids),
        sorted(closed),
    )
    return key, resolves_tick, have_ids


async def _maybe_post_world_catastrophe(bot: Bot, engine, world: dict) -> None:
    """Глобальная катастрофа: сначала все события в БД, потом сдвиг расписания, потом посты.

    Неполный fan-out дополняется на следующем опросе без второго цикла бед.
    Расхождение ключей активной волны лечится до resume, а не стопорит fan-out навсегда.
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
        wave_keys = {_wave_pair(ev) for _r, ev in active_pairs}
        if len(wave_keys) != 1:
            key, resolves_tick, have_ids = _heal_divergent_catastrophe_wave(
                engine, world, active_pairs
            )
        else:
            key, resolves_tick = next(iter(wave_keys))
            have_ids = {int(r["id"]) for r, _ev in active_pairs}
        meta = CATASTROPHES.get(key) or {}
        narrative = meta.get("canned_narrative") or ""
        wave_expired = resolves_tick <= tick_index
        window_t = max(1, resolves_tick - tick_index) if resolves_tick >= tick_index else 1
        for realm in realms:
            if int(realm["id"]) in have_ids:
                continue
            payload: dict = {"threshold_hint": True}
            if key == "cattle_plague":
                payload = {"mitigated_fief_ids": []}
            if wave_expired:
                # Sync-placeholder: не открываем active, который сразу получит fail-штрафы.
                engine.db.create_event(
                    realm_id=realm["id"],
                    kind="catastrophe",
                    event_key=key,
                    payload=payload,
                    narrative=narrative,
                    status="resolved",
                    resolves_tick=resolves_tick,
                )
                continue
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

        if result_text:
            await announce_realm(bot, int(realm["id"]), result_text)


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
