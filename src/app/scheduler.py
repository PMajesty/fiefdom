"""Фоновый планировщик: дневной тик континента и катастрофы."""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot

from app.config import TIMEZONE, tick_slots
from app.domain.tick_pipeline import needs_economy_wake, needs_resolve_wake
from app.domain.tick_schedule import due_tick_slot
from app.notifier import post_digest, post_realm_public
from app.patch_announce import announce_pending_patches
from app.services.catastrophes import CatastropheAnnounce
from app.wiring import get_engine

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


async def _deliver_raid_notices(bot: Bot, notices: list) -> None:
    """Лички и групповые строки после ночного resolve."""
    seen_public: set[tuple[int, str]] = set()
    for notice in notices:
        kind = getattr(notice, "kind", None) or (
            notice.get("kind") if isinstance(notice, dict) else None
        )
        text = getattr(notice, "text", None) or (
            notice.get("text") if isinstance(notice, dict) else None
        )
        if not text:
            continue
        if kind == "dm":
            user_id = getattr(notice, "user_id", None) or (
                notice.get("user_id") if isinstance(notice, dict) else None
            )
            if user_id:
                try:
                    await bot.send_message(int(user_id), str(text))
                except Exception:
                    logger.warning(
                        "raid night dm failed user=%s", user_id, exc_info=True
                    )
            continue
        if kind == "public":
            realm_id = getattr(notice, "realm_id", None) or (
                notice.get("realm_id") if isinstance(notice, dict) else None
            )
            if not realm_id:
                continue
            key = (int(realm_id), str(text))
            if key in seen_public:
                continue
            seen_public.add(key)
            try:
                await post_realm_public(bot, int(realm_id), str(text))
            except Exception:
                logger.exception("raid night public failed realm=%s", realm_id)


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
    world = engine.default_world()
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
    # economy/resolve без incomplete: добить после crash, не открывая новый слот.
    phase_economy = needs_economy_wake(world)
    phase_resolve = needs_resolve_wake(world)
    # Mid-play: half-tick lock заявок набега + капельные слухи.
    # slot_index is not None: тик уже due - слухи не мешаем в тот же poll.
    if (
        not incomplete
        and not phase_economy
        and not phase_resolve
        and slot_index is None
    ):
        try:
            engine.ensure_play_opened_at(int(world["id"]))
            engine.ensure_rumor_queues_planned(int(world["id"]))
            locked = engine.maybe_lock_raids_at_midpoint(int(world["id"]))
            if locked:
                logger.info(
                    "Locked %s open travel intents at midpoint world=%s",
                    locked,
                    world.get("id"),
                )
        except Exception:
            logger.exception("travel midpoint lock failed")
        try:
            for item in engine.maybe_due_rumors(int(world["id"]), local_now):
                text = item.get("text")
                if not text:
                    continue
                ok = await post_realm_public(
                    bot, int(item["realm_id"]), str(text)
                )
                if ok:
                    raw_lines = item.get("lines") or []
                    lines = [
                        str(x) for x in raw_lines if str(x).strip()
                    ]
                    engine.acknowledge_rumor_posted(
                        int(item["realm_id"]),
                        str(item["due"]),
                        str(text),
                        lines=lines or None,
                    )
        except Exception:
            logger.exception("rumor drip failed")

    if incomplete or phase_economy or phase_resolve or slot_index is not None:
        # incomplete/economy/resolve: догоняем/закрываем без нового слота; иначе due.
        result = engine.run_world_tick(
            int(world["id"]),
            tick_slot=(
                None
                if (incomplete or phase_economy or phase_resolve)
                else slot_index
            ),
        )
        await _deliver_raid_notices(bot, result.get("raid_notices") or [])
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
        world = engine.world(int(world["id"])) or world

    try:
        await _maybe_post_world_catastrophe(bot, engine, world)
    except Exception:
        logger.exception("world catastrophe post failed")

    try:
        await announce_pending_patches(bot)
    except Exception:
        logger.exception("patch announce failed")

    for realm in engine.realms_of_world(int(world["id"])):
        try:
            await _resolve_expired_catastrophes(bot, engine, realm)
        except Exception:
            logger.exception("realm %s catastrophe resolve error", realm.get("id"))


async def _deliver_catastrophe_announce(bot: Bot, announce: CatastropheAnnounce) -> None:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = None
    if announce.key == "bandit_night" and announce.event_id is not None:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Вложить 5 силы",
                        callback_data=f"cat:{announce.event_id}:might5",
                    )
                ]
            ]
        )
    await post_realm_public(
        bot, announce.realm_id, announce.text, reply_markup=kb
    )


async def _maybe_post_world_catastrophe(bot: Bot, engine, world: dict) -> None:
    """Доставка волны: сервис пишет БД, планировщик шлёт в чаты."""
    announces = engine.plan_world_catastrophe(world)
    for announce in announces:
        try:
            await _deliver_catastrophe_announce(bot, announce)
        except Exception:
            logger.exception(
                "catastrophe send failed realm=%s", announce.realm_id
            )


async def _resolve_expired_catastrophes(bot: Bot, engine, realm: dict) -> None:
    for result_text in engine.iter_expired_catastrophe_resolutions(realm):
        await post_realm_public(bot, int(realm["id"]), result_text)
