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
from app.engine import raid_pact_lock_message
from app.handlers.shared import (
    fief_home_kb,
    fief_raid_pact_state,
    format_pact_create_announce,
    get_engine,
    map_realms_kb,
    map_view_kb,
    parse_start_payload,
    post_continent_public,
    post_realm_public,
    reply_game,
    reply_guide,
    reply_map_photo,
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
    "рынок": "send",
    "market": "send",
    "сделка": "send",
    "trade": "send",
    "караван": "send",
    "обоз": "send",
    "caravan": "send",
    "земля": "claim",
    "занять": "claim",
    "расширить": "claim",
    "клейм": "claim",
    "claim": "claim",
    "строить": "build",
    "стройка": "build",
    "build": "build",
    "дозор": "patrol",
    "patrol": "patrol",
    "набег": "raid",
    "raid": "raid",
    "передать": "send",
    "отдать": "send",
    "дар": "send",
    "send": "send",
    "gift": "send",
    "пакт": "pact",
    "pact": "pact",
    "устав": "guide",
    "гайд": "guide",
    "правила": "guide",
    "guide": "guide",
    "слухи": "rumors",
    "слух": "rumors",
    "сплетни": "rumors",
    "rumors": "rumors",
    "rumor": "rumors",
    "владения": "holdings",
    "владение": "holdings",
    "здания": "holdings",
    "клетки": "holdings",
    "holdings": "holdings",
    "меню": "menu",
    "menu": "menu",
}


def clear_pending(user_id: int) -> None:
    pending_actions.pop(user_id, None)


def set_pending(user_id: int, data: dict) -> None:
    pending_actions[user_id] = data


def is_pending_cancel_text(text: str) -> bool:
    return text.strip().lower() in {"отмена", "cancel"}


async def _notify_raid_parties(bot, result) -> None:
    """DM жертве (и перехватчику); блок бота - только warning."""
    targets: list[tuple[int, str]] = [
        (int(result.victim_user_id), result.victim_dm_text()),
    ]
    interceptor_text = result.interceptor_dm_text()
    if (
        interceptor_text
        and result.interceptor_user_id is not None
        and int(result.interceptor_user_id) != int(result.victim_user_id)
    ):
        targets.append((int(result.interceptor_user_id), interceptor_text))

    for chat_id, text in targets:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.warning("raid DM failed chat_id=%s", chat_id, exc_info=True)


def format_claim_button(
    x: int,
    y: int,
    tile_type: str,
    next_tile_count: int,
    *,
    is_overgrown: bool = False,
) -> str:
    """Подпись кнопки занятия: \"А3 Поле · 30 тов.\" (глушь ×2, кроме заросших)."""
    name = B.TILE_NAMES_RU.get(tile_type, tile_type)
    is_wilds = (not is_overgrown) and tile_type == B.TILE_WILDS
    cost = B.claim_cost(next_tile_count, is_wilds=is_wilds)
    return f"{coord_label(x, y)} {name} · {cost} тов."


def format_building_type_label(
    building: str,
    tiles: list[dict] | None = None,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Подпись типа здания с минимальной реальной ценой по клеткам усадьбы."""
    name = B.BUILDING_NAMES_RU.get(building, building)
    if tiles is None:
        cost = B.scaled_building_cost(B.building_upgrade_cost(building, 1), cost_mult)
        return f"{name} · {cost} тов."
    cost = B.cheapest_build_action_cost(building, tiles, cost_mult=cost_mult)
    if cost is not None:
        return f"{name} · {cost} тов."
    has_maxed = any(
        t.get("building") == building
        and int(t.get("building_level") or 0) >= 3
        and not t.get("damaged")
        for t in tiles
    )
    if has_maxed:
        return f"{name} · макс."
    return name


def format_build_cost_label(
    building: str,
    tile: dict,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Стоимость постройки/апгрейда/ремонта на клетке."""
    current = tile.get("building")
    level = int(tile.get("building_level") or 0)
    damaged = bool(tile.get("damaged"))
    if damaged and current:
        return f"{B.repair_cost(current, level)} тов."
    if current and current != building:
        return "занято"
    if not current:
        cost = B.scaled_building_cost(B.building_upgrade_cost(building, 1), cost_mult)
        return f"{cost} тов."
    target = level + 1
    if target > 3:
        return "макс."
    cost = B.scaled_building_cost(B.building_upgrade_cost(building, target), cost_mult)
    return f"{cost} тов."


def format_build_tile_button(
    building: str,
    tile: dict,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Подпись клетки при выборе места стройки."""
    coord = coord_label(tile["x"], tile["y"])
    cost_label = format_build_cost_label(building, tile, cost_mult=cost_mult)
    current = tile.get("building")
    level = int(tile.get("building_level") or 0)
    damaged = bool(tile.get("damaged"))
    if damaged and current:
        bname = B.BUILDING_NAMES_RU.get(current, current)
        return f"{coord} ремонт {bname}{level} · {cost_label}"
    if current and current != building:
        bname = B.BUILDING_NAMES_RU.get(current, current)
        return f"{coord} {bname}{level} · {cost_label}"
    if current:
        return f"{coord} →{level + 1} · {cost_label}"
    return f"{coord} · {cost_label}"


def patrol_confirm_text() -> str:
    cost = int(B.PATROL_COST_MIGHT)
    cost_bit = f"−{cost} силы, " if cost > 0 else ""
    return (
        f"Выставить дозор? Усилит защиту от набегов на {B.PATROL_TICKS} тик(а) "
        f"({cost_bit}1 действие, +{B.PATROL_DEFENSE_BONUS} защиты)."
    )


def patrol_confirm_callback(fief_id: int) -> str:
    return f"pat:{int(fief_id)}:ok"


def patrol_prompt_callback(fief_id: int) -> str:
    return f"pat:{int(fief_id)}"


def pending_cancel_callback(fief_id: int) -> str:
    return f"pend:cancel:{int(fief_id)}"


async def show_status(message: Message, fief_id: int) -> None:
    engine = get_engine()
    text = engine.status_card(fief_id)
    await reply_game(message, text, reply_markup=fief_home_kb(engine, fief_id))


def pending_cancel_kb(fief_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=pending_cancel_callback(fief_id),
                )
            ]
        ]
    )


def patrol_confirm_kb(fief_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=patrol_confirm_callback(fief_id),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"st:{int(fief_id)}",
                ),
            ]
        ]
    )


def raid_confirm_kb(
    fief_id: int, *, show_truce: bool = False, open_truce: bool = False
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=f"radok:{int(fief_id)}",
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=pending_cancel_callback(fief_id),
            ),
        ]
    ]
    if show_truce:
        label = (
            "Перемирие: вкл"
            if open_truce
            else "Перемирие: выкл"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"radtruce:{int(fief_id)}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raid_cancel_intent_kb(fief_id: int, intent_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Снять заявку",
                    callback_data=f"radx:{int(fief_id)}:{int(intent_id)}",
                )
            ]
        ]
    )


def caravan_cancel_intent_kb(fief_id: int, intent_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Вернуть обоз",
                    callback_data=f"cvx:{int(fief_id)}:{int(intent_id)}",
                )
            ]
        ]
    )


def starter_tiles_kb(
    realm_id: int,
    tiles: list[dict],
) -> InlineKeyboardMarkup:
    rows = []
    for t in tiles:
        label = (
            f"{coord_label(t['x'], t['y'])} - "
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
        realm = engine.get_realm(f["realm_id"])
        title = realm["title"] if realm else f"#{f['realm_id']}"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{engine.fief_label(f)} ({title})",
                    callback_data=f"st:{f['id']}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claimable_kb(
    fief_id: int,
    coords: list[tuple[int, int]],
    *,
    next_tile_count: int,
    tile_meta: dict[tuple[int, int], tuple[str, bool]],
) -> InlineKeyboardMarkup:
    """tile_meta: (x,y) → (tile_type, is_overgrown)."""
    rows = []
    row: list[InlineKeyboardButton] = []
    for x, y in coords[:24]:
        tile_type, is_overgrown = tile_meta.get((x, y), (B.TILE_FIELD, False))
        row.append(
            InlineKeyboardButton(
                text=format_claim_button(
                    x, y, tile_type, next_tile_count, is_overgrown=is_overgrown
                ),
                callback_data=f"clm:{fief_id}:{x}:{y}",
            )
        )
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def building_types_kb(
    fief_id: int,
    tiles: list[dict] | None = None,
    *,
    cost_mult: float = 1.0,
) -> InlineKeyboardMarkup:
    rows = []
    for key in B.PLAYER_BUILDINGS:
        rows.append(
            [
                InlineKeyboardButton(
                    text=format_building_type_label(key, tiles, cost_mult=cost_mult),
                    callback_data=f"bld:{fief_id}:{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gather_resources_kb(fief_id: int) -> InlineKeyboardMarkup:
    from app.domain.resources import resource_defs

    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    for rdef in resource_defs():
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{rdef.name_ru} +{B.gather_amount(rdef.key)}",
                    callback_data=f"gth:{fid}:{rdef.key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def demolish_tiles_kb(fief_id: int, tiles: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for t in tiles[:24]:
        building = t.get("building")
        level = int(t.get("building_level") or 0)
        if not building or level <= 0:
            continue
        if building == B.BLD_MANOR or t.get("is_core"):
            continue
        name = B.BUILDING_NAMES_RU.get(building, building)
        refund = B.demolish_refund_goods(str(building), level)
        label = f"{coord_label(t['x'], t['y'])} {name}{level} · +{refund}"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"dml:{fief_id}:{t['x']}:{t['y']}",
            )
        )
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    if not rows:
        rows.append(
            [InlineKeyboardButton(text="Нечего сносить", callback_data=f"st:{fief_id}")]
        )
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_tiles_kb(
    fief_id: int,
    building: str,
    tiles: list[dict],
    *,
    cost_mult: float = 1.0,
) -> InlineKeyboardMarkup:
    rows = []
    row: list[InlineKeyboardButton] = []
    for t in tiles[:24]:
        row.append(
            InlineKeyboardButton(
                text=format_build_tile_button(building, t, cost_mult=cost_mult),
                callback_data=f"bld:{fief_id}:{building}:{t['x']}:{t['y']}",
            )
        )
        if len(row) >= 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="< Назад", callback_data=f"bld:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raid_targets_kb(fief_id: int, others: list[dict], engine=None) -> InlineKeyboardMarkup:
    from app.domain.rumors import might_soft_label

    rows = []
    for o in others[:20]:
        label = engine.fief_label(o) if engine is not None else o["name"]
        if o.get("via_portal") and engine is not None:
            realm = engine.get_realm(o["realm_id"]) or {}
            title = str(realm.get("title") or "долина")[:12]
            label = f"{title}: {label}"
        soft = might_soft_label(int(o.get("might") or 0))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{label} · {soft}",
                    callback_data=f"rad:{fief_id}:{o['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fief_id}")])
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
    rows.append([InlineKeyboardButton(text="< Меню", callback_data=f"st:{fief_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def pact_invite_kb(target_fief_id: int, invite_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Принять",
                    callback_data=f"pct:acc:{target_fief_id}:{invite_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"pct:dec:{target_fief_id}:{invite_id}",
                ),
            ]
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    engine = get_engine()
    user = message.from_user
    try:
        engine.ensure_user(user)
        kind, rid = parse_start_payload(command.args)

        if kind == "join" and rid is not None:
            realm = engine.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            existing = engine.fief_of_user_in_realm(user.id, rid)
            if existing:
                engine.remember_last_realm(user.id, rid)
                await show_status(message, existing["id"])
                return
            world_id = realm.get("world_id")
            owned = (
                engine.fief_of_user_in_world(user.id, int(world_id))
                if world_id is not None
                else None
            )
            if owned:
                engine.remember_last_realm(user.id, owned["realm_id"])
                await answer_html(
                    message,
                    "У вас уже есть усадьба на континенте. "
                    "Вторая недоступна - открываю вашу.",
                )
                await show_status(message, owned["id"])
                return
            tiles = engine.starter_tile_choices(rid, 3)
            if not tiles:
                await answer_html(message, "Нет свободных стартовых клеток.")
                return
            await answer_html(
                message,
                f"Выберите стартовую клетку в долине \"{realm['title']}\":",
                reply_markup=starter_tiles_kb(rid, tiles),
            )
            return

        if kind == "realm" and rid is not None:
            realm = engine.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            fief = engine.fief_of_user_in_realm(user.id, rid)
            if fief:
                engine.remember_last_realm(user.id, rid)
                await show_status(message, fief["id"])
                return
            world_id = realm.get("world_id")
            owned = (
                engine.fief_of_user_in_world(user.id, int(world_id))
                if world_id is not None
                else None
            )
            if owned:
                engine.remember_last_realm(user.id, owned["realm_id"])
                await answer_html(
                    message,
                    "У вас уже есть усадьба на континенте. "
                    "Вторая недоступна - открываю вашу.",
                )
                await show_status(message, owned["id"])
                return
            tiles = engine.starter_tile_choices(rid, 3)
            if not tiles:
                await answer_html(message, "Нет свободных стартовых клеток.")
                return
            await answer_html(
                message,
                f"Усадьбы ещё нет. Выберите клетку в \"{realm['title']}\":",
                reply_markup=starter_tiles_kb(rid, tiles),
            )
            return

        fiefs = engine.fiefs_of_user(user.id)
        if not fiefs:
            await answer_html(
                message,
                "У вас пока нет усадьбы.\n"
                "В групповом чате долины нажмите \"Моё владение\" или /вч_я.",
            )
            return
        if len(fiefs) > 1:
            await answer_html(
                message,
                "Выберите усадьбу:",
                reply_markup=realm_picker_kb(fiefs, engine),
            )
            return
        engine.remember_last_realm(user.id, fiefs[0]["realm_id"])
        await show_status(message, fiefs[0]["id"])
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_start")
        await answer_html(message, "Ошибка /start.")


# В личке также: /вч_гайд через общий текст устава
@router.message(Command("вч_гайд", "вч_устав", "vch_guide", "vch_rules", "гайд", "устав"))
async def cmd_dm_guide(message: Message) -> None:
    engine = get_engine()
    fief = resolve_fief_for_user(engine, message.from_user.id)
    kb = fief_home_kb(engine, fief["id"]) if fief else None
    await reply_guide(message, engine.guide_text(), reply_markup=kb)


@router.message(Command("меню", "menu"))
async def cmd_menu(message: Message) -> None:
    engine = get_engine()
    fief = resolve_fief_for_user(engine, message.from_user.id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    await show_status(message, fief["id"])


@router.message(F.text, ~F.text.startswith("/"))
async def dm_text(message: Message) -> None:
    """FSM ввода + текстовое меню (slash-команды не перехватываем)."""
    if not message.text:
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
            "Команды: статус, карта, земля, строить, дозор, набег, "
            "караван, передать, пакт, слухи, владения, устав, меню.",
        )
        return

    fief = resolve_fief_for_user(engine, user_id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    fid = fief["id"]

    try:
        if key == "menu":
            await show_status(message, fid)
        elif key == "status":
            await show_status(message, fid)
        elif key == "map":
            realm = engine.get_realm(fief["realm_id"])
            world_id = realm.get("world_id") if realm else None
            if world_id is None:
                await reply_map_photo(
                    message,
                    engine,
                    engine.map_photo(fief["realm_id"], highlight_fief_id=fid),
                    reply_markup=map_view_kb(fid),
                )
            else:
                realms = engine.realms_of_world(int(world_id))
                await reply_game(
                    message,
                    "Карты долин континента - выберите долину:",
                    reply_markup=map_realms_kb(
                        fid, realms, home_realm_id=int(fief["realm_id"])
                    ),
                )
        elif key == "guide":
            await reply_guide(
                message,
                engine.guide_text(),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "rumors":
            await reply_game(
                message,
                engine.rumors_text(fief["realm_id"]),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "holdings":
            await reply_game(
                message,
                engine.holdings_text(fid),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "claim":
            await _offer_claim(message, engine, fief)
        elif key == "build":
            tiles, cost_mult = engine.build_options(fid)
            await answer_html(
                message,
                "Выберите здание:",
                reply_markup=building_types_kb(fid, tiles, cost_mult=cost_mult),
            )
        elif key == "patrol":
            await answer_html(
                message,
                patrol_confirm_text(),
                reply_markup=patrol_confirm_kb(fid),
            )
        elif key == "raid":
            await _offer_raid(message, engine, fief)
        elif key == "send":
            await _offer_send(message, engine, fief)
        elif key == "pact":
            await _offer_pact(message, engine, fief)
    except ValueError as exc:
        await answer_html(message, str(exc), reply_markup=fief_home_kb(engine, fid))
    except Exception:
        logger.exception("dm_text menu")
        await answer_html(message, "Ошибка команды.")


async def _offer_send(message: Message, engine, fief: dict) -> None:
    set_pending(
        message.from_user.id,
        {
            "kind": "send_target",
            "fief_id": fief["id"],
            "realm_id": fief["realm_id"],
        },
    )
    await reply_game(
        message,
        "Куда отправить обоз с зерном или товарами?\n"
        "Напишите id усадьбы, имя или @username.\n"
        "Обоз идёт до следующего колокола тика; пока в пути - можно вернуть. "
        f"От {B.CARAVAN_PUBLIC_AMOUNT} и больше долина увидит выезд; "
        "мелкое - только адресату. Силу везти нельзя.\n"
        "Или напишите \"отмена\".",
        reply_markup=pending_cancel_kb(fief["id"]),
    )


async def _offer_claim(message: Message, engine, fief: dict) -> None:
    views = engine.tile_views(fief["realm_id"])
    owned = {
        (t.x, t.y)
        for t in views
        if t.owner_fief_id == fief["id"] and not t.is_overgrown
    }
    by_xy = {(t.x, t.y): t for t in views}
    realm = engine.get_realm(fief["realm_id"])
    claimable = sorted(
        adjacent_claimable(
            owned,
            by_xy,
            width=realm["width"],
            height=realm["height"],
            for_fief_id=fief["id"],
        )
    )
    if not claimable:
        await answer_html(message, "Нет соседних клеток для занятия.")
        return
    tile_meta = {
        (x, y): (by_xy[(x, y)].tile_type, by_xy[(x, y)].is_overgrown)
        for x, y in claimable
        if (x, y) in by_xy
    }
    await answer_html(
        message,
        "Выберите клетку для занятия:",
        reply_markup=claimable_kb(
            fief["id"],
            claimable,
            next_tile_count=len(owned) + 1,
            tile_meta=tile_meta,
        ),
    )


async def _offer_raid(message: Message, engine, fief: dict) -> None:
    open_, _hint = fief_raid_pact_state(engine, fief)
    if not open_:
        realm = engine.get_realm(fief["realm_id"])
        day_number = int(realm["day_number"]) if realm else 1
        await answer_html(
            message,
            raid_pact_lock_message(
                onboard_step=int(fief.get("onboard_step") or 0),
                day_number=day_number,
            ),
            reply_markup=fief_home_kb(engine, fief["id"]),
        )
        return
    others = engine.list_raid_target_fiefs(int(fief["id"]))
    if not others:
        await answer_html(message, "Некого грабить.")
        return
    await answer_html(
        message,
        "Выберите цель набега (любая долина континента).\n"
        "Точная сила скрыта - смотрите слухи или спрашивайте. "
        "Защита цели - дружина на месте, сторожка, дозор и перехват пакта.",
        reply_markup=raid_targets_kb(fief["id"], others, engine),
    )


async def _offer_pact(message: Message, engine, fief: dict) -> None:
    open_, _hint = fief_raid_pact_state(engine, fief)
    if not open_:
        realm = engine.get_realm(fief["realm_id"])
        day_number = int(realm["day_number"]) if realm else 1
        await answer_html(
            message,
            raid_pact_lock_message(
                onboard_step=int(fief.get("onboard_step") or 0),
                day_number=day_number,
            ),
            reply_markup=fief_home_kb(engine, fief["id"]),
        )
        return
    in_pact = bool(fief.get("pact_id"))
    is_founder = False
    if in_pact:
        pact = engine.get_pact(fief["pact_id"])
        is_founder = bool(pact and pact["founder_fief_id"] == fief["id"])
        name = pact["name"] if pact else "?"
        text = f"Пакт \"{name}\"."
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

    if is_pending_cancel_text(text):
        clear_pending(user_id)
        fid = pending.get("fief_id")
        if fid is not None:
            await show_status(message, int(fid))
        else:
            await answer_html(message, "Отменено.")
        return True

    if kind == "raid_might":
        might = int(text.strip())
        fief_id = int(pending["fief_id"])
        victim_id = int(pending["victim_id"])
        fief = engine.db.get_fief(fief_id) or {}
        men_home = max(0, int(fief.get("might") or 0) - might)
        set_pending(
            user_id,
            {
                "kind": "raid_confirm",
                "fief_id": fief_id,
                "victim_id": victim_id,
                "might": might,
                "open_truce": False,
            },
        )
        vic = engine.db.get_fief(victim_id)
        vic_name = engine.fief_label(vic) if vic else str(victim_id)
        world = engine.db.get_world(engine._world_id_for_realm(int(fief["realm_id"])))
        lock_text = engine._format_raid_deadline(world or {}, midpoint=True)
        resolve_text = engine._format_raid_deadline(world or {}, midpoint=False)
        truce_note = ""
        if not fief.get("pact_id"):
            truce_note = (
                "\nМожно включить открытое перемирие с другими одиночками "
                "на ту же цель."
            )
        elif fief.get("pact_id"):
            truce_note = "\nСоюзники по пакту сольются в один удар на дороге."
        await reply_game(
            message,
            (
                f"Подтвердите набег на {vic_name}.\n"
                f"Уйдёт {might} силы, дома останется {men_home}.\n"
                f"Отмена заявки до {lock_text}; бой около {resolve_text}."
                f"{truce_note}"
            ),
            reply_markup=raid_confirm_kb(
                fief_id, show_truce=not bool(fief.get("pact_id"))
            ),
        )
        return True

    if kind == "raid_confirm":
        # Подтверждение только кнопками; текст - подсказка.
        await reply_game(
            message,
            "Нажмите \"Подтвердить\" или \"Отмена\" под сообщением выше.",
            reply_markup=raid_confirm_kb(
                int(pending["fief_id"]),
                show_truce=not bool(
                    (engine.db.get_fief(int(pending["fief_id"])) or {}).get("pact_id")
                ),
            ),
        )
        return True

    if kind == "send_target":
        target = _resolve_fief_ref(engine, pending["realm_id"], text)
        if not target:
            await answer_html(
                message,
                "Усадьба не найдена. Id, имя или @username.\n"
                "Или напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        if int(target["id"]) == int(pending["fief_id"]):
            await answer_html(
                message,
                "Нельзя слать обоз себе. Укажите другую усадьбу.\n"
                "Или напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        set_pending(
            user_id,
            {
                "kind": "send_amount",
                "fief_id": pending["fief_id"],
                "realm_id": pending["realm_id"],
                "target_fief_id": target["id"],
            },
        )
        await reply_game(
            message,
            f"Получатель: <b>{engine.fief_label(target)}</b>\n"
            "Сколько положить в обоз? Формат: <code>зерно 10</code> или "
            "<code>товары 5</code>.\n"
            "Силу везти нельзя. Или напишите \"отмена\".",
            reply_markup=pending_cancel_kb(pending["fief_id"]),
        )
        return True

    if kind == "send_amount":
        parsed = _parse_send_line(text)
        if not parsed:
            await reply_game(
                message,
                "Формат: <code>зерно 10</code> или <code>товары 5</code>.\n"
                "Или напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        res, amt = parsed
        sender = engine.db.get_fief(pending["fief_id"])
        receiver = engine.db.get_fief(pending["target_fief_id"])
        result = engine.declare_caravan(
            pending["fief_id"], pending["target_fief_id"], res, amt
        )
        clear_pending(user_id)
        await reply_game(
            message,
            result.dm_text,
            reply_markup=caravan_cancel_intent_kb(
                pending["fief_id"], result.intent_id
            ),
        )
        if receiver:
            engine.ensure_user(message.from_user)
            try:
                await message.bot.send_message(
                    int(receiver["user_id"]),
                    result.receiver_dm_text,
                )
            except Exception:
                logger.warning(
                    "caravan DM to receiver %s failed", receiver.get("user_id")
                )
        if sender and result.is_public and result.public_declare_text:
            await post_continent_public(
                message.bot,
                sender["realm_id"],
                result.public_declare_text,
            )
        return True

    if kind == "pact_name":
        fief = engine.db.get_fief(pending["fief_id"])
        pact_name = text.strip()[:40]
        msg = engine.create_pact(pending["fief_id"], text)
        clear_pending(user_id)
        await reply_game(
            message, msg, reply_markup=fief_home_kb(engine, pending["fief_id"])
        )
        if fief and pact_name:
            engine.ensure_user(message.from_user)
            await post_realm_public(
                message.bot,
                fief["realm_id"],
                format_pact_create_announce(engine.fief_label(fief), pact_name),
            )
        return True

    if kind == "pact_invite":
        # id усадьбы или имя
        target = _resolve_fief_ref(engine, pending["realm_id"], text)
        if not target:
            await answer_html(
                message,
                "Усадьба не найдена. Укажите id или имя.\nИли напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        founder = engine.fief_by_id(pending["fief_id"])
        pact = (
            engine.get_pact(founder["pact_id"])
            if founder and founder.get("pact_id")
            else None
        )
        invite = engine.invite_to_pact(pending["fief_id"], target["id"])
        clear_pending(user_id)
        await reply_game(
            message,
            f"Приглашение отправлено: {engine.fief_label(target)}.",
            reply_markup=fief_home_kb(engine, pending["fief_id"]),
        )
        if founder and pact:
            try:
                await message.bot.send_message(
                    int(target["user_id"]),
                    f"Вас приглашают в пакт \"{pact['name']}\" "
                    f"(от {engine.fief_label(founder)}).",
                    reply_markup=pact_invite_kb(int(target["id"]), int(invite["id"])),
                )
            except Exception:
                logger.warning(
                    "send pact invite DM to %s failed", target.get("user_id")
                )
        return True

    return False


def _tradeable_res_map() -> dict[str, str]:
    from app.domain.resources import synonym_to_key

    return synonym_to_key(tradeable_only=True)


def _send_re() -> re.Pattern[str]:
    from app.domain.resources import tradeable_synonym_alternatives

    alt = tradeable_synonym_alternatives()
    return re.compile(rf"^({alt})\s+(\d+)$", re.IGNORECASE)


def _parse_send_line(text: str) -> tuple[str, int] | None:
    m = _send_re().match(text.strip())
    if not m:
        return None
    return _tradeable_res_map()[m.group(1).lower()], int(m.group(2))


def _resolve_fief_ref(engine, realm_id: int, text: str) -> dict | None:
    """Ищет усадьбу на всём континенте (своя долина + остальные долины мира)."""
    text = text.strip()
    realm_ids = {int(realm_id)}
    for nb in engine.db.list_adjacent_realms(int(realm_id)):
        realm_ids.add(int(nb["id"]))
    if text.isdigit():
        f = engine.db.get_fief(int(text))
        if f and int(f["realm_id"]) in realm_ids:
            return f
        return None
    needle = text.lower()
    for rid in sorted(realm_ids):
        for f in engine.db.list_fiefs(rid):
            label = engine.fief_label(f)
            if f["name"].lower() == needle or label.lower() == needle:
                return f
            user = engine.db.get_user(f["user_id"])
            uname = (user.get("username") or "").strip().lower() if user else ""
            if uname and needle in {uname, f"@{uname}", f"усадьба @{uname}"}:
                return f
    return None
