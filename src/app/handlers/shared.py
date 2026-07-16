"""Общие хелперы хендлеров: движок, админ, deep-link, realm/fief."""
from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import ADMIN_USER_ID
from app.database import get_db
from app.engine import Engine
from app.messaging import answer_html, send_html

logger = logging.getLogger(__name__)

_engine: Engine | None = None

_START_REALM = re.compile(r"^realm_(\d+)$", re.IGNORECASE)
_START_JOIN = re.compile(r"^join_(\d+)$", re.IGNORECASE)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_db())
    return _engine


def is_admin(user_id: int | None) -> bool:
    if user_id is None or ADMIN_USER_ID is None:
        return False
    return int(user_id) == int(ADMIN_USER_ID)


def parse_start_payload(payload: str | None) -> tuple[str | None, int | None]:
    """Разбор deep-link: realm_{id} | join_{realm_id} → (kind, id)."""
    if not payload:
        return None, None
    text = payload.strip()
    m = _START_JOIN.match(text)
    if m:
        return "join", int(m.group(1))
    m = _START_REALM.match(text)
    if m:
        return "realm", int(m.group(1))
    return None, None


def resolve_realm_for_user(engine: Engine, user_id: int, chat: Any = None) -> dict | None:
    """Realm из группового чата, last_realm пользователя или единственной усадьбы."""
    db = engine.db
    if chat is not None:
        chat_type = getattr(chat, "type", None)
        chat_id = getattr(chat, "id", None)
        if chat_type in ("group", "supergroup") and chat_id is not None:
            return db.get_realm_by_chat(chat_id)

    user = db.get_user(user_id)
    if user and user.get("last_realm_id"):
        realm = db.get_realm(user["last_realm_id"])
        if realm:
            return realm

    fiefs = db.list_fiefs_by_user(user_id)
    if len(fiefs) == 1:
        return db.get_realm(fiefs[0]["realm_id"])
    return None


def resolve_fief_for_user(
    engine: Engine,
    user_id: int,
    realm_id: int | None = None,
) -> dict | None:
    db = engine.db
    if realm_id is not None:
        return db.get_fief_by_user(realm_id, user_id)
    realm = resolve_realm_for_user(engine, user_id)
    if realm:
        return db.get_fief_by_user(realm["id"], user_id)
    fiefs = db.list_fiefs_by_user(user_id)
    if len(fiefs) == 1:
        return fiefs[0]
    return None


def deep_link_url(bot_username: str, payload: str) -> str:
    return f"https://t.me/{bot_username}?start={payload}"


def main_menu_kb(fief_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Статус", callback_data=f"st:{fid}"),
                InlineKeyboardButton(text="Карта", callback_data=f"map:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Рынок", callback_data=f"mkt:{fid}"),
                InlineKeyboardButton(text="Клейм", callback_data=f"clm:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Стройка", callback_data=f"bld:{fid}"),
                InlineKeyboardButton(text="Дозор", callback_data=f"pat:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Набег", callback_data=f"rad:{fid}"),
                InlineKeyboardButton(text="Сделка", callback_data=f"trd:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Пакт", callback_data=f"pct:{fid}"),
            ],
        ]
    )


async def reply_game(message: Message, text: str, **kwargs: Any) -> None:
    """Ответ с HTML от движка (теги не экранируем)."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return
    kwargs.pop("parse_mode", None)
    try:
        await message.answer(plain, parse_mode=ParseMode.HTML, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("reply_game: HTML rejected, fallback answer_html: %s", exc)
        await answer_html(message, plain, **kwargs)
    except Exception as exc:
        logger.error("reply_game failed: %s", exc)


async def send_game(bot, chat_id: int, text: str, **kwargs: Any) -> None:
    """Отправка HTML от движка в чат."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return
    kwargs.pop("parse_mode", None)
    try:
        await bot.send_message(chat_id, plain, parse_mode=ParseMode.HTML, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("send_game: HTML rejected, fallback send_html: %s", exc)
        await send_html(bot, chat_id, plain, **kwargs)
    except Exception as exc:
        logger.error("send_game failed: %s", exc)
