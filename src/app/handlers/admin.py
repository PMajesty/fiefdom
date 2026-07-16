"""Админ-команды (только личка + ADMIN_USER_ID)."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import Message

from app.domain.digest import format_decree
from app.domain.events import MINOR_EVENTS
from app.handlers.shared import get_engine, is_admin, post_digest, reply_game, send_game
from app.messaging import answer_html, escape_html

logger = logging.getLogger(__name__)

router = Router(name="admin")
router.message.filter(F.chat.type == ChatType.PRIVATE)


def _require_admin(message: Message) -> bool:
    return is_admin(message.from_user.id if message.from_user else None)


def format_realms_list(realms: list[dict[str, Any]], fief_counts: dict[int, int]) -> str:
    if not realms:
        return "Нет долин."
    lines = ["Долины:"]
    for realm in realms:
        rid = int(realm["id"])
        title = escape_html(str(realm.get("title") or "без названия"))
        chat_id = realm.get("chat_id")
        n_fiefs = fief_counts.get(rid, 0)
        lines.append(
            f"#{rid} {title}\n"
            f"  chat_id=<code>{chat_id}</code> · усадеб: {n_fiefs}"
        )
    return "\n".join(lines)


ADMIN_HELP_TEXT = (
    "Админ-команды (только личка). Сначала узнай id долины:\n"
    "<code>/вч_realms</code>\n"
    "\n"
    "<b>Тик континента</b> (все долины сразу):\n"
    "<code>/вч_tick</code>\n"
    "\n"
    "<b>Выдать ресурсы</b> усадьбе (зерно товары сила):\n"
    "<code>/вч_grant 1 3 50 20 10</code>\n"
    "\n"
    "<b>Событие</b> до следующего тика (ключ из списка при ошибке):\n"
    "<code>/вч_event 1 harvest</code>\n"
    "\n"
    "<b>Стереть континент</b> (все долины мира) - два шага:\n"
    "1) <code>/вч_wipe_start 1</code> - id любой долины-якоря\n"
    "2) скопируй и отправь команду с кодом + словом УДАЛИТЬ\n"
    "\n"
    "<b>Новая долина</b>: в группе <code>/вотчина</code> - "
    "автоматически встаёт на дорогу порталов рядом со случайной.\n"
    "\n"
    "<b>Заморозка</b> усадьбы: 1 = заморозить, 0 = снять:\n"
    "<code>/вч_freeze 3 1</code>\n"
    "\n"
    "<b>Указ</b> в групповой чат долины:\n"
    "<code>/вч_decree 1 Текст указа</code>"
)


@router.message(Command("вч_admin_help"))
async def cmd_admin_help(message: Message) -> None:
    if not _require_admin(message):
        return
    await reply_game(message, ADMIN_HELP_TEXT)


@router.message(Command("вч_realms"))
async def cmd_realms(message: Message) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        realms = engine.db.list_realms()
        fief_counts = {
            int(r["id"]): len(engine.db.list_fiefs(int(r["id"]))) for r in realms
        }
        await reply_game(message, format_realms_list(realms, fief_counts))
    except Exception:
        logger.exception("cmd_realms")
        await answer_html(message, "Ошибка списка долин.")


@router.message(Command("вч_tick"))
async def cmd_tick(message: Message, bot: Bot) -> None:
    if not _require_admin(message):
        return
    engine = get_engine()
    try:
        world = engine.db.get_or_create_world()
        result = engine.run_world_tick(int(world["id"]))
        n = 0
        for item in result.get("realms") or []:
            digest = item.get("digest") or ""
            chat_id = item.get("chat_id")
            realm_id = item.get("realm_id")
            if digest and chat_id and realm_id:
                await post_digest(bot, chat_id, int(realm_id), digest)
                n += 1
        await answer_html(
            message,
            f"Тик континента выполнен ({n} сводок).",
        )
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
        world_id = engine._world_id_for_realm(realm_id)
        if key in MINOR_EVENTS:
            meta = MINOR_EVENTS[key]
            engine.db.update_world(
                world_id,
                active_minor_key=key,
                active_minor_until=None,
            )
            engine.db.sync_realms_clock_from_world(world_id)
            note = (
                f"Событие континента \"{meta['name_ru']}\" до следующего тика. "
                f"{meta['mechanics']}"
            )
        else:
            engine.db.update_world(
                world_id,
                active_minor_key=key,
                active_minor_until=None,
            )
            engine.db.sync_realms_clock_from_world(world_id)
            note = f"Ключ события континента \"{key}\" до следующего тика."
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
            raise ValueError(
                "Сначала шаг 1: <code>/вч_wipe_start 1</code> "
                "(подставь id из /вч_realms). Бот пришлёт команду с кодом."
            )
        realm_id = int(parts[1])
        if not engine.db.get_realm(realm_id):
            raise ValueError("Долина не найдена. Смотри /вч_realms")
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
            raise ValueError(
                "Это шаг 2. Сначала <code>/вч_wipe_start 1</code>, "
                "потом отправь команду из ответа бота целиком "
                "(id + код + УДАЛИТЬ)."
            )
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
