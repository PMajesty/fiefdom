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
from app.messaging import send_game
from app.notifier import post_continent_public, post_digest, post_realm_public
from app.patch_announce import announce_pending_patches
from app.services.catastrophes import CatastropheAnnounce
from app.wiring import get_engine

logger = logging.getLogger(__name__)

POLL_SECONDS = 30


async def _finalize_caravan_lock_announce(bot: Bot, engine, world_id: int) -> None:
    """Mid-play: доставить midday-обозы, затем всегда закоммитить флаги."""
    lock_announce = engine.announce_locked_caravans(int(world_id))
    if lock_announce.notices:
        delivered = await _deliver_raid_notices(bot, lock_announce.notices)
        if not delivered:
            logger.warning(
                "caravan lock announce delivery had failures world=%s",
                world_id,
            )
    # Commit всегда после попытки: иначе retry спамит continent.
    if lock_announce.intent_ids:
        engine.commit_locked_caravan_announcements(
            lock_announce.intent_ids,
            public_ids=lock_announce.public_ids,
        )
        logger.info(
            "Announced %s locked caravan intents world=%s",
            lock_announce.announced_intent_count,
            world_id,
        )


async def _deliver_raid_notices(bot: Bot, notices: list) -> bool:
    """Лички и групповые строки (ночь + midday-confirm обозов).

    False если хотя бы одна доставка упала (только для логов; commit не зависит).
    """
    ok = True
    seen_public: set[tuple[int, str]] = set()
    seen_continent: set[tuple[int, str]] = set()
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
                    ok = False
                    logger.warning(
                        "raid night dm failed user=%s", user_id, exc_info=True
                    )
            continue
        if kind == "continent":
            realm_id = getattr(notice, "realm_id", None) or (
                notice.get("realm_id") if isinstance(notice, dict) else None
            )
            if not realm_id:
                continue
            key = (int(realm_id), str(text))
            if key in seen_continent:
                continue
            seen_continent.add(key)
            try:
                posted = await post_continent_public(
                    bot, int(realm_id), str(text)
                )
                if not posted:
                    ok = False
            except Exception:
                ok = False
                logger.exception(
                    "caravan continent public failed realm=%s", realm_id
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
                posted = await post_realm_public(bot, int(realm_id), str(text))
                if not posted:
                    ok = False
            except Exception:
                ok = False
                logger.exception("raid night public failed realm=%s", realm_id)
    return ok


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
    # Пока закреплён досрок, плановый слот не трогаем - ждём early_tick_at.
    if world.get("early_tick_at") is not None:
        slot_index = None
    else:
        slot_index = due_tick_slot(
            local_now=local_now,
            last_tick_local_date=_as_date(world.get("last_tick_local_date")),
            last_tick_slot=(
                int(world["last_tick_slot"])
                if world.get("last_tick_slot") is not None
                else None
            ),
            slots=tick_slots(),
        )
    incomplete = engine.world_tick_incomplete(int(world["id"]))
    # economy/resolve без incomplete: добить после crash, не открывая новый слот.
    phase_economy = needs_economy_wake(world)
    phase_resolve = needs_resolve_wake(world)
    early_due = (
        not incomplete
        and not phase_economy
        and not phase_resolve
        and engine.early_tick_due(world)
    )
    # Mid-play: half-tick lock заявок набега + капельные слухи.
    # slot_index/early_due: тик уже due - слухи не мешаем в тот же poll.
    if (
        not incomplete
        and not phase_economy
        and not phase_resolve
        and slot_index is None
        and not early_due
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
            await _finalize_caravan_lock_announce(
                bot, engine, int(world["id"])
            )
        except Exception:
            logger.exception("travel midpoint lock failed")
        try:
            early_lock = engine.reconcile_early_tick_quorum(int(world["id"]))
            if (
                early_lock is not None
                and early_lock.locked
                and early_lock.early_tick_at is not None
            ):
                world = engine.world(int(world["id"])) or world
                text = engine.early_tick_lock_announcement(
                    early_lock.early_tick_at, world
                )
                for uid in early_lock.notify_user_ids:
                    try:
                        await send_game(bot, int(uid), text)
                    except Exception:
                        logger.warning(
                            "early tick notify failed user=%s",
                            uid,
                            exc_info=True,
                        )
        except Exception:
            logger.exception("early tick quorum reconcile failed")
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

    if (
        incomplete
        or phase_economy
        or phase_resolve
        or slot_index is not None
        or early_due
    ):
        # incomplete/economy/resolve: догоняем/закрываем без нового слота;
        # early_due: полный тик; слот только если досрок съедает конец окна.
        if incomplete or phase_economy or phase_resolve:
            # Досрок мог записать pending_slot до crash mid-resolve.
            tick_slot = engine.pending_early_tick_slot(world)
        elif early_due:
            # Пока тик incomplete, early_due выключен - повторного запуска нет.
            tick_slot = engine.tick_slot_for_early_fire(world)
            engine.arm_early_tick_fire(int(world["id"]), tick_slot)
        else:
            tick_slot = slot_index
        result = engine.run_world_tick(
            int(world["id"]),
            tick_slot=tick_slot,
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
            "World tick slot %s posted for %s realms (resumed=%s incomplete=%s early=%s)",
            tick_slot,
            len([x for x in (result.get("realms") or []) if not x.get("skipped")]),
            result.get("resumed"),
            result.get("incomplete"),
            early_due,
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
