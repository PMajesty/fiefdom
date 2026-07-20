"""Клавиатуры DM-потоков: клейм, стройка, набег, пакт, отмены."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import balance as B
from app.domain.map_gen import coord_label
from app.domain.rumors import might_soft_label
from app.ui.keyboards.labels import (
    format_build_tile_button,
    format_building_type_label,
    format_claim_button,
)


def pending_cancel_callback(fief_id: int) -> str:
    return f"pend:cancel:{int(fief_id)}"


def patrol_confirm_callback(fief_id: int) -> str:
    return f"pat:{int(fief_id)}:ok"


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


def realm_picker_kb(entries: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Выбор усадьбы: (fief_id, подпись кнопки)."""
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"st:{int(fief_id)}",
            )
        ]
        for fief_id, label in entries
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claimable_kb(
    fief_id: int,
    coords: list[tuple[int, int]],
    *,
    next_tile_count: int,
    tile_meta: dict[tuple[int, int], tuple[str, bool]],
) -> InlineKeyboardMarkup:
    """tile_meta: (x,y) -> (tile_type, is_overgrown)."""
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
    from app.domain.resource_registry import resource_defs


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
        if building == B.BLD_MANOR:
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


def raid_targets_kb(fief_id: int, targets: list[dict]) -> InlineKeyboardMarkup:
    """Цели набега: каждый dict - id, label, might."""
    rows = []
    for o in targets[:20]:
        soft = might_soft_label(int(o.get("might") or 0))
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{o['label']} · {soft}",
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
