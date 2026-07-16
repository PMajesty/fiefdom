"""HTML-хелперы для отправки сообщений через aiogram (ParseMode.HTML)."""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4000


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def chunk_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


async def _send_with_retry(coro_factory, *, label: str) -> Any:
    """Один повтор при TelegramRetryAfter; иначе пробрасывает исключение."""
    try:
        return await coro_factory()
    except TelegramRetryAfter as exc:
        logger.warning("%s: flood wait %.1fs, retry once", label, exc.retry_after)
        await asyncio.sleep(float(exc.retry_after))
        return await coro_factory()


async def answer_html(message: Message, text: str, **kwargs: Any) -> None:
    """Отправляет plain-текст: экранирует и шлёт HTML; при BadRequest - plain."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return

    kwargs.pop("parse_mode", None)
    reply_markup = kwargs.pop("reply_markup", None)
    for index, part in enumerate(chunk_text(plain)):
        escaped = escape_html(part)
        part_kwargs = dict(kwargs)
        if index == 0 and reply_markup is not None:
            part_kwargs["reply_markup"] = reply_markup

        async def _html(p: str = escaped, kw: dict = part_kwargs):
            return await message.answer(p, parse_mode=ParseMode.HTML, **kw)

        try:
            await _send_with_retry(_html, label="answer_html")
        except TelegramBadRequest as exc:
            logger.warning("answer_html: HTML rejected, plain fallback: %s", exc)

            async def _plain(p: str = part, kw: dict = part_kwargs):
                return await message.answer(p, **kw)

            try:
                await _send_with_retry(_plain, label="answer_html/plain")
            except Exception as fallback_exc:
                logger.error("answer_html: plain fallback failed: %s", fallback_exc)
        except Exception as exc:
            logger.error("answer_html: send failed: %s", exc)


async def send_html(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> None:
    """То же, что answer_html, но через bot.send_message(chat_id=...)."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return

    kwargs.pop("parse_mode", None)
    reply_markup = kwargs.pop("reply_markup", None)
    for index, part in enumerate(chunk_text(plain)):
        escaped = escape_html(part)
        part_kwargs = dict(kwargs)
        if index == 0 and reply_markup is not None:
            part_kwargs["reply_markup"] = reply_markup

        async def _html(p: str = escaped, kw: dict = part_kwargs):
            return await bot.send_message(chat_id, p, parse_mode=ParseMode.HTML, **kw)

        try:
            await _send_with_retry(_html, label="send_html")
        except TelegramBadRequest as exc:
            logger.warning("send_html: HTML rejected, plain fallback: %s", exc)

            async def _plain(p: str = part, kw: dict = part_kwargs):
                return await bot.send_message(chat_id, p, **kw)

            try:
                await _send_with_retry(_plain, label="send_html/plain")
            except Exception as fallback_exc:
                logger.error("send_html: plain fallback failed: %s", fallback_exc)
        except Exception as exc:
            logger.error("send_html: send failed: %s", exc)
