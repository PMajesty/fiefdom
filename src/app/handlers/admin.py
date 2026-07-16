"""Админ-команды (только личка + ADMIN_USER_ID)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.domain.digest import format_decree
from app.domain.events import MINOR_EVENTS
from app.handlers.shared import get_engine, is_admin, post_digest, reply_game, send_game
from app.messaging import answer_html

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE)


def _require_admin(message: Message) -> bool:
    return is_admin(message.from_user.id if message.from_user else None)


@router.message(Command("вч_admin_help"))
async def cmd_admin_help(message: Message) -> None:
    if not _require_admin(message):
        return
    await reply_game(
        message,
        "Админ:\n"
        "<code>/вч_tick [realm_id]</code>\n"
        "<code>/вч_grant realm_id fief_id grain goods might</code>\n"
        "<code>/вч_event realm_id key</code>\n"
        "<code>/вч_wipe_start realm_id</code>\n"
        "<code>/вч_wipe realm_id CODE УДАЛИТЬ</code>\n"
        "<code>/вч_freeze fief_id 0|1</code>\n"
        "<code>/вч_decree realm_id текст...</code>",
    )


@router.message(Command("вч_tick"))
async def cmd_tick(message: Message, bot: Bot) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split()
        if len(parts) >= 2:
            realm_id = int(parts[1])
            realms = [engine.db.get_realm(realm_id)]
            if not realms[0]:
                raise ValueError("Долина не найдена")
        else:
            realms = engine.db.list_realms()
            if not realms:
                raise ValueError("Нет долин")

        for realm in realms:
            result = engine.run_realm_tick(realm["id"])
            digest = result.get("digest") or ""
            chat_id = result.get("chat_id") or realm["chat_id"]
            if digest and chat_id:
                await post_digest(bot, chat_id, realm["id"], digest)
            await answer_html(message, f"Тик realm={realm['id']} выполнен.")
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_tick")
        await answer_html(message, "Ошибка тика.")


@router.message(Command("вч_grant"))
async def cmd_grant(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split()
        if len(parts) < 6:
            raise ValueError("Формат: /вч_grant realm_id fief_id grain goods might")
        realm_id = int(parts[1])
        fief_id = int(parts[2])
        grain = int(parts[3])
        goods = int(parts[4])
        might = int(parts[5])
        fief = engine.db.get_fief(fief_id)
        if not fief or fief["realm_id"] != realm_id:
            raise ValueError("Усадьба не найдена в этой долине")
        engine.db.update_fief(
            fief_id,
            grain=fief["grain"] + grain,
            goods=fief["goods"] + goods,
            might=fief["might"] + might,
        )
        await answer_html(
            message,
            f"Выдано усадьбе #{fief_id}: +{grain} зерна, +{goods} товаров, +{might} силы.",
        )
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_grant")
        await answer_html(message, "Ошибка выдачи.")


@router.message(Command("вч_event"))
async def cmd_event(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            keys = ", ".join(sorted(MINOR_EVENTS.keys()))
            raise ValueError(f"Формат: /вч_event realm_id key\nКлючи: {keys}")
        realm_id = int(parts[1])
        key = parts[2].strip()
        realm = engine.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        from datetime import datetime, timedelta, timezone

        if key in MINOR_EVENTS:
            meta = MINOR_EVENTS[key]
            engine.db.update_realm(
                realm_id,
                active_minor_key=key,
                active_minor_until=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            note = f"Событие «{meta['name_ru']}» активно 24ч. {meta['mechanics']}"
        else:
            engine.db.update_realm(
                realm_id,
                active_minor_key=key,
                active_minor_until=datetime.now(timezone.utc) + timedelta(hours=24),
            )
            note = f"Установлен ключ события «{key}» на 24ч."
        await answer_html(message, note)
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_event")
        await answer_html(message, "Ошибка события.")


@router.message(Command("вч_wipe_start"))
async def cmd_wipe_start(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split()
        if len(parts) < 2:
            raise ValueError("Формат: /вч_wipe_start realm_id")
        realm_id = int(parts[1])
        if not engine.db.get_realm(realm_id):
            raise ValueError("Долина не найдена")
        msg = engine.begin_wipe(realm_id)
        await reply_game(message, msg)
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_wipe_start")
        await answer_html(message, "Ошибка.")


@router.message(Command("вч_wipe"))
async def cmd_wipe(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split()
        if len(parts) < 4:
            raise ValueError("Формат: /вч_wipe realm_id CODE УДАЛИТЬ")
        realm_id = int(parts[1])
        code = parts[2]
        word = parts[3]
        msg = engine.confirm_wipe(realm_id, code, word)
        await answer_html(message, msg)
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_wipe")
        await answer_html(message, "Ошибка удаления.")


@router.message(Command("вч_freeze"))
async def cmd_freeze(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split()
        if len(parts) < 3:
            raise ValueError("Формат: /вч_freeze fief_id 0|1")
        fief_id = int(parts[1])
        flag = int(parts[2])
        if flag not in (0, 1):
            raise ValueError("Флаг: 0 или 1")
        fief = engine.db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        engine.db.update_fief(fief_id, frozen=bool(flag))
        state = "заморожена" if flag else "разморожена"
        await answer_html(message, f"Усадьба #{fief_id} {state}.")
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_freeze")
        await answer_html(message, "Ошибка.")


@router.message(Command("вч_decree"))
async def cmd_decree(message: Message, bot: Bot) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        parts = (message.text or "").split(maxsplit=2)
        if len(parts) < 3:
            raise ValueError("Формат: /вч_decree realm_id текст...")
        realm_id = int(parts[1])
        body = parts[2].strip()
        realm = engine.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        number = engine.db.next_decree_number(realm_id)
        engine.db.add_decree(realm_id, number, body)
        text = format_decree(number, body)
        await send_game(bot, realm["chat_id"], text)
        await answer_html(message, f"Указ №{number} опубликован.")
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_decree")
        await answer_html(message, "Ошибка указа.")
