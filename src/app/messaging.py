"""HTML-хелперы для отправки сообщений через aiogram (ParseMode.HTML)."""
from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4000
GUIDE_DOCUMENT_FILENAME = "ustav.txt"
GUIDE_DOCUMENT_CAPTION = "📜 Краткий устав Вотчины"


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


async def answer_text_document(
    message: Message,
    text: str,
    *,
    filename: str,
    caption: str | None = None,
    **kwargs: Any,
) -> None:
    """Отправляет длинный plain-текст одним .txt-документом."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return

    kwargs.pop("parse_mode", None)
    payload = plain.encode("utf-8")

    async def _send():
        # Новый буфер на каждую попытку: после RetryAfter старый может быть уже прочитан
        document = BufferedInputFile(payload, filename=filename)
        return await message.answer_document(
            document,
            caption=caption,
            **kwargs,
        )

    try:
        await _send_with_retry(_send, label="answer_text_document")
    except Exception as exc:
        logger.error("answer_text_document: send failed: %s", exc)


async def reply_guide_document(
    message: Message,
    text: str,
    **kwargs: Any,
) -> None:
    """Устав целиком как ustav.txt - без HTML и без нарезки на сообщения."""
    await answer_text_document(
        message,
        text,
        filename=GUIDE_DOCUMENT_FILENAME,
        caption=GUIDE_DOCUMENT_CAPTION,
        **kwargs,
    )


def _is_stale_file_id_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "wrong file identifier",
        "file_id",
        "file identifier",
        "file reference",
        "wrong remote file id",
    )
    return any(marker in text for marker in markers)


async def answer_photo_bytes(
    message: Message,
    png_bytes: bytes,
    *,
    filename: str = "map.png",
    caption: str | None = None,
    file_id: str | None = None,
    **kwargs: Any,
) -> Message | None:
    """Шлёт PNG: сначала по file_id, иначе BufferedInputFile; возвращает Message."""
    kwargs.pop("parse_mode", None)
    photo_ref: str | BufferedInputFile
    if file_id:
        photo_ref = file_id
    else:
        photo_ref = BufferedInputFile(png_bytes, filename=filename)

    async def _send(ref: str | BufferedInputFile = photo_ref):
        return await message.answer_photo(ref, caption=caption, **kwargs)

    try:
        return await _send_with_retry(_send, label="answer_photo_bytes")
    except TelegramBadRequest as exc:
        if not file_id or not _is_stale_file_id_error(exc):
            logger.error("answer_photo_bytes: send failed: %s", exc)
            return None
        logger.warning("answer_photo_bytes: stale file_id, re-upload: %s", exc)

        async def _resend():
            return await message.answer_photo(
                BufferedInputFile(png_bytes, filename=filename),
                caption=caption,
                **kwargs,
            )

        try:
            return await _send_with_retry(_resend, label="answer_photo_bytes/reupload")
        except Exception as fallback_exc:
            logger.error("answer_photo_bytes: re-upload failed: %s", fallback_exc)
            return None
    except Exception as exc:
        logger.error("answer_photo_bytes: send failed: %s", exc)
        return None
