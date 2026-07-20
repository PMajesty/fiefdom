"""Публичные уведомления в групповые чаты долин (digest, deep-link)."""
from __future__ import annotations

import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.messaging import send_game
from app.wiring import get_engine

logger = logging.getLogger(__name__)


def deep_link_url(bot_username: str, payload: str) -> str:
    return f"https://t.me/{bot_username}?start={payload}"


def open_estate_kb(bot_username: str, realm_id: int) -> InlineKeyboardMarkup:
    """Кнопка deep-link в личку: \"Открыть усадьбу\"."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть усадьбу",
                    url=deep_link_url(bot_username, f"realm_{int(realm_id)}"),
                )
            ]
        ]
    )


async def bot_username_or_none(bot) -> str | None:
    try:
        me = await bot.get_me()
        return me.username or None
    except Exception:
        logger.warning("bot_username_or_none: get_me failed", exc_info=True)
        return None


async def post_digest(bot, chat_id: int, realm_id: int, digest: str) -> None:
    """Публикует сводку в группу; кнопка deep-link - если есть username бота."""
    kb = None
    username = await bot_username_or_none(bot)
    if username:
        kb = open_estate_kb(username, realm_id)
    await send_game(bot, chat_id, digest, reply_markup=kb)


async def post_realm_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> bool:
    """Короткое объявление в групповой чат долины. Ошибки не роняют хендлер."""
    if not text or not realm_id:
        return False
    try:
        engine = get_engine()
        realm = engine.get_realm(int(realm_id))
        if not realm:
            return False
        chat_id = realm.get("chat_id")
        if chat_id is None:
            return False
        return bool(
            await send_game(bot, int(chat_id), text, reply_markup=reply_markup)
        )
    except Exception:
        logger.warning(
            "post_realm_public failed realm_id=%s", realm_id, exc_info=True
        )
        return False


async def announce_realm(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Короткое объявление владельцам усадеб долины в личку. Ошибки не роняют хендлер."""
    if not text:
        return
    try:
        engine = get_engine()
        for fief in engine.fiefs_of_realm(int(realm_id)):
            uid = fief.get("user_id")
            if not uid:
                continue
            try:
                await send_game(
                    bot, int(uid), text, reply_markup=reply_markup
                )
            except Exception:
                logger.warning(
                    "announce_realm dm failed user=%s realm_id=%s",
                    uid,
                    realm_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning("announce_realm failed realm_id=%s", realm_id, exc_info=True)


async def announce_continent(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Объявление всем усадьбам континента (своя долина и остальные долины мира)."""
    if not text:
        return
    seen: set[int] = set()
    try:
        engine = get_engine()
        targets = [int(realm_id), *engine.adjacent_realm_ids(int(realm_id))]
        for rid in targets:
            if rid in seen:
                continue
            seen.add(rid)
            await announce_realm(bot, rid, text, reply_markup=reply_markup)
    except Exception:
        logger.warning(
            "announce_continent failed realm_id=%s", realm_id, exc_info=True
        )


async def post_continent_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Крупный обоз: в групповые чаты всех долин континента."""
    if not text:
        return
    seen: set[int] = set()
    try:
        engine = get_engine()
        targets = [int(realm_id), *engine.adjacent_realm_ids(int(realm_id))]
        for rid in targets:
            if rid in seen:
                continue
            seen.add(rid)
            await post_realm_public(bot, rid, text, reply_markup=reply_markup)
    except Exception:
        logger.warning(
            "post_continent_public failed realm_id=%s", realm_id, exc_info=True
        )
