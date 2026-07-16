"""Личка: /start, меню текстом, простая FSM для ввода."""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import balance as B
from app.domain.map_gen import coord_label
from app.domain.economy import adjacent_claimable
from app.handlers.shared import (
    get_engine,
    main_menu_kb,
    parse_start_payload,
    reply_game,
    resolve_fief_for_user,
)
from app.messaging import answer_html

logger = logging.getLogger(__name__)

router = Router(name="dm")
router.message.filter(F.chat.type == ChatType.PRIVATE)

# Простая FSM в памяти процесса: user_id -> {kind, ...}
pending_actions: dict[int, dict] = {}

_MENU_WORDS = {
    "статус": "status",
    "status": "status",
    "карта": "map",
    "map": "map",
    "рынок": "market",
    "market": "market",
    "клейм": "claim",
    "claim": "claim",
    "стройка": "build",
    "build": "build",
    "дозор": "patrol",
    "patrol": "patrol",
    "набег": "raid",
    "raid": "raid",
    "сделка": "trade",
    "trade": "trade",
    "пакт": "pact",
    "pact": "pact",
    "меню": "menu",
    "menu": "menu",
}


def clear_pending(user_id: int) -> None:
    pending_actions.pop(user_id, None)


def set_pending(user_id: int, data: dict) -> None:
    pending_actions[user_id] = data


async def show_status(message: Message, fief_id: int) -> None:
    engine = get_engine()
    text = engine.status_card(fief_id)
    await reply_game(message, text, reply_markup=main_menu_kb(fief_id))


def starter_tiles_kb(realm_id: int, tiles: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for t in tiles:
        label = (
            f"{coord_label(t['x'], t['y'])} — "
            f"{B.TILE_NAMES_RU.get(t['tile_type'], t['tile_type'])}"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"pick:{realm_id}:{t['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def realm_picker_kb(fiefs: list[dict], engine) -> InlineKeyboardMarkup:
    rows = []
    for f in fiefs:
        realm = engine.db.get_realm(f["realm_id"])
        title = realm["title"] if realm else f"#{f['realm_id']}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{f['name']} ({title})",
                    callback_data=f"st:{f['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claimable_kb(fief_id: int, coords: list[tuple[int, int]]) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for x, y in coords[:24]:
        row.append(
            InlineKeyboardButton(
                text=coord_label(x, y),
                callback_data=f"clm:{fief_id}:{x}:{y}",
            )
        )
        if len(row) >= 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def building_types_kb(fief_id: int) -> InlineKeyboardMarkup:
    rows = []
    for key, name in B.BUILDING_NAMES_RU.items():
        rows.append(
            [
                InlineKeyboardButton(
                    text=name,
                    callback_data=f"bld:{fief_id}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_tiles_kb(fief_id: int, building: str, tiles: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for t in tiles[:24]:
        bl = t.get("building")
        lvl = int(t.get("building_level") or 0)
        suffix = f" {B.BUILDING_NAMES_RU.get(bl, bl)}{lvl}" if bl else ""
        row.append(
            InlineKeyboardButton(
                text=f"{coord_label(t['x'], t['y'])}{suffix}",
                callback_data=f"bld:{fief_id}:{building}:{t['x']}:{t['y']}",
            )
        )
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="« Назад", callback_data=f"bld:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raid_targets_kb(fief_id: int, others: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for o in others[:20]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{o['name']} (сила {o['might']})",
                    callback_data=f"rad:{fief_id}:{o['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def market_kb(fief_id: int, offers: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Создать лот", callback_data=f"trd:new:{fief_id}"),
        ]
    ]
    for o in offers[:12]:
        mine = o["offerer_fief_id"] == fief_id
        if mine:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Отменить #{o['id']}",
                        callback_data=f"trd:c:{fief_id}:{o['id']}",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Принять #{o['id']}",
                        callback_data=f"trd:a:{fief_id}:{o['id']}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pact_kb(fief_id: int, in_pact: bool, is_founder: bool) -> InlineKeyboardMarkup:
    rows = []
    if not in_pact:
        rows.append(
            [InlineKeyboardButton(text="Создать пакт", callback_data=f"pct:new:{fief_id}")]
        )
    else:
        if is_founder:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="Пригласить",
                        callback_data=f"pct:inv:{fief_id}",
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(text="Прикрытие вкл", callback_data=f"pct:cov:{fief_id}:1"),
                InlineKeyboardButton(text="выкл", callback_data=f"pct:cov:{fief_id}:0"),
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="Выйти из пакта", callback_data=f"pct:leave:{fief_id}")]
        )
    rows.append([InlineKeyboardButton(text="« Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    engine = get_engine()
    user = message.from_user
    try:
        engine.ensure_user(user)
        kind, rid = parse_start_payload(command.args)

        if kind == "join" and rid is not None:
            realm = engine.db.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            existing = engine.db.get_fief_by_user(rid, user.id)
            if existing:
                engine.db.set_last_realm(user.id, rid)
                await show_status(message, existing["id"])
                return
            tiles = engine.starter_tile_choices(rid, 3)
            if not tiles:
                await answer_html(message, "Нет свободных стартовых клеток.")
                return
            await answer_html(
                message,
                f"Выберите стартовую клетку в долине «{realm['title']}»:",
                reply_markup=starter_tiles_kb(rid, tiles),
            )
            return

        if kind == "realm" and rid is not None:
            realm = engine.db.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            engine.db.set_last_realm(user.id, rid)
            fief = engine.db.get_fief_by_user(rid, user.id)
            if fief:
                await show_status(message, fief["id"])
            else:
                tiles = engine.starter_tile_choices(rid, 3)
                if not tiles:
                    await answer_html(message, "Нет свободных стартовых клеток.")
                    return
                await answer_html(
                    message,
                    f"Усадьбы ещё нет. Выберите клетку в «{realm['title']}»:",
                    reply_markup=starter_tiles_kb(rid, tiles),
                )
            return

        fiefs = engine.db.list_fiefs_by_user(user.id)
        if not fiefs:
            await answer_html(
                message,
                "У вас пока нет усадьбы.\n"
                "В групповом чате долины нажмите «Моё владение» или /вч_я.",
            )
            return
        if len(fiefs) > 1:
            await answer_html(
                message,
                "Выберите усадьбу:",
                reply_markup=realm_picker_kb(fiefs, engine),
            )
            return
        engine.db.set_last_realm(user.id, fiefs[0]["realm_id"])
        await show_status(message, fiefs[0]["id"])
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_start")
        await answer_html(message, "Ошибка /start.")


@router.message(Command("меню", "menu"))
async def cmd_menu(message: Message) -> None:
    engine = get_engine()
    fief = resolve_fief_for_user(engine, message.from_user.id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    await answer_html(message, "Меню усадьбы:", reply_markup=main_menu_kb(fief["id"]))


@router.message(F.text)
async def dm_text(message: Message) -> None:
    """FSM ввода + текстовое меню."""
    if not message.text or message.text.startswith("/"):
        return
    user_id = message.from_user.id
    text = message.text.strip()
    engine = get_engine()

    pending = pending_actions.get(user_id)
    if pending:
        try:
            handled = await _handle_pending(message, engine, pending, text)
            if handled:
                return
        except ValueError as exc:
            clear_pending(user_id)
            await answer_html(message, str(exc))
            return
        except Exception:
            logger.exception("pending action")
            clear_pending(user_id)
            await answer_html(message, "Действие сорвалось.")
            return

    key = _MENU_WORDS.get(text.lower())
    if not key:
        await answer_html(
            message,
            "Команды: статус, карта, рынок, клейм, стройка, дозор, набег, сделка, пакт, меню.",
        )
        return

    fief = resolve_fief_for_user(engine, user_id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    fid = fief["id"]

    try:
        if key == "menu":
            await answer_html(message, "Меню:", reply_markup=main_menu_kb(fid))
        elif key == "status":
            await show_status(message, fid)
        elif key == "map":
            await reply_game(
                message,
                engine.map_text(fief["realm_id"], highlight_fief_id=fid),
                reply_markup=main_menu_kb(fid),
            )
        elif key == "market":
            offers = engine.db.list_open_trades(fief["realm_id"], fid)
            await reply_game(
                message,
                engine.market_text(fief["realm_id"], fid),
                reply_markup=market_kb(fid, offers),
            )
        elif key == "claim":
            await _offer_claim(message, engine, fief)
        elif key == "build":
            await answer_html(
                message,
                "Выберите здание:",
                reply_markup=building_types_kb(fid),
            )
        elif key == "patrol":
            msg = engine.patrol(fid)
            await reply_game(message, msg, reply_markup=main_menu_kb(fid))
        elif key == "raid":
            await _offer_raid(message, engine, fief)
        elif key == "trade":
            offers = engine.db.list_open_trades(fief["realm_id"], fid)
            await reply_game(
                message,
                engine.market_text(fief["realm_id"], fid),
                reply_markup=market_kb(fid, offers),
            )
        elif key == "pact":
            await _offer_pact(message, engine, fief)
    except ValueError as exc:
        await answer_html(message, str(exc), reply_markup=main_menu_kb(fid))
    except Exception:
        logger.exception("dm_text menu")
        await answer_html(message, "Ошибка команды.")


async def _offer_claim(message: Message, engine, fief: dict) -> None:
    views = engine.tile_views(fief["realm_id"])
    owned = {
        (t.x, t.y)
        for t in views
        if t.owner_fief_id == fief["id"] and not t.is_overgrown
    }
    claimable = sorted(
        adjacent_claimable(owned, {(t.x, t.y): t for t in views}, for_fief_id=fief["id"])
    )
    if not claimable:
        await answer_html(message, "Нет соседних клеток для клейма.")
        return
    await answer_html(
        message,
        "Выберите клетку:",
        reply_markup=claimable_kb(fief["id"], claimable),
    )


async def _offer_raid(message: Message, engine, fief: dict) -> None:
    others = [
        o
        for o in engine.db.list_fiefs(fief["realm_id"])
        if o["id"] != fief["id"] and not o.get("frozen")
    ]
    if not others:
        await answer_html(message, "Некого грабить.")
        return
    await answer_html(
        message,
        "Выберите цель набега:",
        reply_markup=raid_targets_kb(fief["id"], others),
    )


async def _offer_pact(message: Message, engine, fief: dict) -> None:
    in_pact = bool(fief.get("pact_id"))
    is_founder = False
    if in_pact:
        pact = engine.db.get_pact(fief["pact_id"])
        is_founder = bool(pact and pact["founder_fief_id"] == fief["id"])
        name = pact["name"] if pact else "?"
        text = f"Пакт «{name}»."
    else:
        text = "Вы не в пакте."
    await answer_html(
        message,
        text,
        reply_markup=pact_kb(fief["id"], in_pact, is_founder),
    )


async def _handle_pending(message: Message, engine, pending: dict, text: str) -> bool:
    kind = pending.get("kind")
    user_id = message.from_user.id

    if kind == "raid_might":
        might = int(text.strip())
        msg = engine.raid(pending["fief_id"], pending["victim_id"], might)
        clear_pending(user_id)
        await reply_game(message, msg, reply_markup=main_menu_kb(pending["fief_id"]))
        return True

    if kind == "trade_create":
        # формат: зерно 10 товары 5  ИЛИ  grain 10 goods 5
        parsed = _parse_trade_line(text)
        if not parsed:
            await reply_game(
                message,
                "Формат: <code>зерно 10 товары 5</code> (отдаю → хочу).",
            )
            return True
        give_res, give_amt, want_res, want_amt = parsed
        msg = engine.post_trade(
            pending["fief_id"], give_res, give_amt, want_res, want_amt
        )
        clear_pending(user_id)
        await reply_game(message, msg, reply_markup=main_menu_kb(pending["fief_id"]))
        return True

    if kind == "pact_name":
        msg = engine.create_pact(pending["fief_id"], text)
        clear_pending(user_id)
        await reply_game(message, msg, reply_markup=main_menu_kb(pending["fief_id"]))
        return True

    if kind == "pact_invite":
        # id усадьбы или имя
        target = _resolve_fief_ref(engine, pending["realm_id"], text)
        if not target:
            await answer_html(message, "Усадьба не найдена. Укажите id или имя.")
            return True
        msg = engine.invite_to_pact(pending["fief_id"], target["id"])
        clear_pending(user_id)
        await reply_game(message, msg, reply_markup=main_menu_kb(pending["fief_id"]))
        return True

    return False


_TRADE_RE = re.compile(
    r"^(зерно|товары|grain|goods)\s+(\d+)\s+(зерно|товары|grain|goods)\s+(\d+)$",
    re.IGNORECASE,
)

_RES_MAP = {
    "зерно": B.RES_GRAIN,
    "grain": B.RES_GRAIN,
    "товары": B.RES_GOODS,
    "goods": B.RES_GOODS,
}


def _parse_trade_line(text: str) -> tuple[str, int, str, int] | None:
    m = _TRADE_RE.match(text.strip())
    if not m:
        return None
    give_res = _RES_MAP[m.group(1).lower()]
    want_res = _RES_MAP[m.group(3).lower()]
    return give_res, int(m.group(2)), want_res, int(m.group(4))


def _resolve_fief_ref(engine, realm_id: int, text: str) -> dict | None:
    text = text.strip()
    if text.isdigit():
        f = engine.db.get_fief(int(text))
        if f and f["realm_id"] == realm_id:
            return f
        return None
    for f in engine.db.list_fiefs(realm_id):
        if f["name"].lower() == text.lower():
            return f
    return None
