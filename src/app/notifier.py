"""Публичные уведомления долины: fan-out в личку владельцам усадеб."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.messaging import send_game
from app.wiring import get_engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FanoutResult:
    """Итог fan-out по долине."""

    ok: bool
    targets: int
    sent: int = 0

    def __bool__(self) -> bool:
        return self.ok


def rumor_fanout_should_ack(result: FanoutResult) -> bool:
    """Слух: не ретраим при пустой долине или хотя бы одной принятой личке.

    Жёсткий сбой fan-out (ok=False при targets=0) не подтверждает доставку.
    """
    return result.ok or result.sent > 0


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


async def _fanout_realm_dms(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> FanoutResult:
    """Шлёт текст владельцам усадеб долины."""
    if not text or not realm_id:
        return FanoutResult(ok=False, targets=0, sent=0)
    try:
        engine = get_engine()
        failures = 0
        targets = 0
        sent = 0
        for fief in engine.fiefs_of_realm(int(realm_id)):
            uid = fief.get("user_id")
            if not uid:
                continue
            targets += 1
            try:
                if await send_game(
                    bot, int(uid), text, reply_markup=reply_markup
                ):
                    sent += 1
                else:
                    failures += 1
            except Exception:
                failures += 1
                logger.warning(
                    "realm dm fan-out failed user=%s realm_id=%s",
                    uid,
                    realm_id,
                    exc_info=True,
                )
        return FanoutResult(
            ok=failures == 0, targets=targets, sent=sent
        )
    except Exception:
        logger.warning(
            "realm dm fan-out failed realm_id=%s", realm_id, exc_info=True
        )
        return FanoutResult(ok=False, targets=0, sent=0)


async def post_digest(bot, realm_id: int, digest: str) -> None:
    """Сводка владельцам усадеб долины; кнопка deep-link - если есть username бота."""
    kb = None
    username = await bot_username_or_none(bot)
    if username:
        kb = open_estate_kb(username, realm_id)
    await _fanout_realm_dms(bot, int(realm_id), digest, reply_markup=kb)


async def post_realm_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> FanoutResult:
    """Короткое объявление владельцам усадеб долины в личку. Ошибки не роняют хендлер."""
    return await _fanout_realm_dms(
        bot, realm_id, text, reply_markup=reply_markup
    )


async def announce_realm(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> FanoutResult:
    """Алиас post_realm_public (историческое имя fan-out в личку)."""
    return await post_realm_public(
        bot, realm_id, text, reply_markup=reply_markup
    )


async def post_continent_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> bool:
    """Крупный обоз: в лички всех усадеб континента."""
    if not text:
        return False
    seen: set[int] = set()
    all_ok = True
    try:
        engine = get_engine()
        targets = [int(realm_id), *engine.adjacent_realm_ids(int(realm_id))]
        for rid in targets:
            if rid in seen:
                continue
            seen.add(rid)
            if not await post_realm_public(
                bot, rid, text, reply_markup=reply_markup
            ):
                all_ok = False
    except Exception:
        logger.warning(
            "post_continent_public failed realm_id=%s", realm_id, exc_info=True
        )
        return False
    return all_ok


async def announce_continent(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> bool:
    """Алиас post_continent_public (историческое имя fan-out в личку)."""
    return await post_continent_public(
        bot, realm_id, text, reply_markup=reply_markup
    )
