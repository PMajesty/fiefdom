"""Групповые команды долины."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import tick_slots
from app.domain.tick_schedule import format_tick_slots
from app.handlers.shared import (
    deep_link_url,
    get_engine,
    is_admin,
    open_estate_kb,
    reply_game,
    reply_guide,
    reply_map_photo,
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
        await reply_map_photo(
            message,
            engine,
            engine.map_photo(realm["id"], highlight_fief_id=highlight),
        )
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
async def cmd_digest(message: Message, bot: Bot) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        tz = realm.get("timezone") or "Europe/Moscow"
        last = (realm.get("last_digest_text") or "").strip()
        if last:
            text = last
        else:
            text = (
                f"Сводка публикуется автоматически после тиков "
                f"в {format_tick_slots(tick_slots())} ({tz}).\n"
                "Откройте усадьбу в личке - там задания, рынок и новости дня."
            )
        if is_admin(message.from_user.id if message.from_user else None):
            text += (
                f"\nДосрочный тик: кнопка \"Тик сейчас\" в личке (голоса континента). "
                f"Админ: <code>/вч_tick {realm['id']}</code>."
            )
        kb = None
        username = await _bot_username(message, bot)
        if username and username != "bot":
            kb = open_estate_kb(username, realm["id"])
        await reply_game(message, text, reply_markup=kb)
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


@router.message(Command("вч_гайд", "вч_устав", "vch_guide", "vch_rules"))
async def cmd_guide(message: Message) -> None:
    try:
        await reply_guide(message, get_engine().guide_text())
    except Exception:
        logger.exception("cmd_guide")
        await answer_html(message, "Устав временно недоступен.")


@router.message(Command("вч_я", "vch_me"))
async def cmd_me(message: Message, bot: Bot) -> None:
    engine = get_engine()
    try:
        realm = engine.db.get_realm_by_chat(message.chat.id)
        if not realm:
            await answer_html(message, "Долина ещё не основана. /вотчина")
            return
        username = await _bot_username(message, bot)
        fief = engine.db.get_fief_by_user(realm["id"], message.from_user.id)
        if fief:
            payload = f"realm_{realm['id']}"
            label = "Открыть усадьбу"
        else:
            owned = engine.db.list_fiefs_by_user(message.from_user.id)
            if owned:
                payload = f"realm_{owned[0]['realm_id']}"
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
