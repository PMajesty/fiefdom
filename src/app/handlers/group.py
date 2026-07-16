"""Групповые команды долины."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.shared import (
    deep_link_url,
    get_engine,
    is_admin,
    reply_game,
    resolve_fief_for_user,
)
from app.messaging import answer_html

logger = logging.getLogger(__name__)

router = Router(name="group")
router.message.filter(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))


async def _bot_username(message: Message, bot: Bot) -> str:
    me = await (message.bot or bot).get_me()
    return me.username or "bot"


@router.message(Command("вотчина"))
async def cmd_create_realm(message: Message, bot: Bot) -> None:
    engine = get_engine()
    try:
        engine.ensure_user(message.from_user)
        title = (message.chat.title or "Долина").strip()
        realm, msg = engine.create_realm(message.chat.id, title, message.from_user.id)
        username = await _bot_username(message, bot)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Моё владение",
                        url=deep_link_url(username, f"join_{realm['id']}"),
                    )
                ]
            ]
        )
        await reply_game(message, msg, reply_markup=kb)
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_create_realm")
        await answer_html(message, "Не удалось основать долину.")


@router.message(Command("вч_карта", "vch_map"))
async def cmd_map(message: Message) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        fief = resolve_fief_for_user(engine, message.from_user.id, realm["id"])
        highlight = fief["id"] if fief else None
        await reply_game(message, engine.map_text(realm["id"], highlight_fief_id=highlight))
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_map")
        await answer_html(message, "Не удалось показать карту.")


@router.message(Command("вч_рынок", "vch_market"))
async def cmd_market(message: Message) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        fief = resolve_fief_for_user(engine, message.from_user.id, realm["id"])
        fid = fief["id"] if fief else None
        await reply_game(message, engine.market_text(realm["id"], fid))
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_market")
        await answer_html(message, "Не удалось показать рынок.")


@router.message(Command("вч_сводка", "vch_digest"))
async def cmd_digest(message: Message) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        hour = int(realm.get("tick_hour") or 13)
        minute = int(realm.get("tick_minute") or 0)
        tz = realm.get("timezone") or "Europe/Moscow"
        text = (
            f"Сводка публикуется автоматически после дневного тика "
            f"в {hour:02d}:{minute:02d} ({tz})."
        )
        if is_admin(message.from_user.id):
            text += (
                f"\nАдмин: форс-тик — <code>/вч_tick {realm['id']}</code> в личке бота."
            )
        await reply_game(message, text)
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_digest")
        await answer_html(message, "Не удалось ответить.")


@router.message(Command("вч_помощь", "vch_help"))
async def cmd_help(message: Message) -> None:
    try:
        await reply_game(message, get_engine().help_text())
    except Exception:
        logger.exception("cmd_help")
        await answer_html(message, "Справка временно недоступна.")


@router.message(Command("вч_я", "vch_me"))
async def cmd_me(message: Message, bot: Bot) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        username = await _bot_username(message, bot)
        fief = resolve_fief_for_user(engine, message.from_user.id, realm["id"])
        if fief:
            payload = f"realm_{realm['id']}"
            label = "Открыть усадьбу"
        else:
            payload = f"join_{realm['id']}"
            label = "Получить усадьбу"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=label, url=deep_link_url(username, payload))]
            ]
        )
        await answer_html(
            message,
            "Нажмите кнопку, чтобы открыть личку с ботом.",
            reply_markup=kb,
        )
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_me")
        await answer_html(message, "Не удалось создать ссылку.")
