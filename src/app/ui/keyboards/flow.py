"""Клавиатуры DM-потоков: клейм, стройка, набег, пакт, отмены."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import balance as B
from app.domain.map_gen import coord_label
from app.domain.rumors import might_soft_label
from app.ui.keyboards.chrome import (
    menu_button,
    menu_callback,
    menu_row,
    pending_cancel_callback,
    with_menu_footer,
    with_pending_footer,
)
from app.ui.keyboards.labels import (
    format_build_tile_button,
    format_building_type_label,
    format_claim_button,
)
from app.ui.keyboards.transfer import transfer_cancel_intent_kb


def patrol_confirm_callback(fief_id: int) -> str:
    return f"pat:{int(fief_id)}:ok"


def disband_confirm_callback(fief_id: int, keep: int) -> str:
    return f"dis:{int(fief_id)}:{int(keep)}:ok"


def pending_cancel_kb(fief_id: int) -> InlineKeyboardMarkup:
    return with_pending_footer([], fief_id)


def patrol_confirm_kb(fief_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=patrol_confirm_callback(fid),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=menu_callback(fid),
                ),
            ],
            menu_row(fid),
        ]
    )


def disband_militia_kb(fief_id: int, might: int) -> InlineKeyboardMarkup:
    """Кнопки роспуска: до бесплатного потолка и до нуля."""
    fid = int(fief_id)
    current = max(0, int(might))
    rows: list[list[InlineKeyboardButton]] = []
    free = int(B.MILITIA_FREE)
    if current > free:
        lost = current - free
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Оставить {free} (−{lost})",
                    callback_data=disband_confirm_callback(fid, free),
                )
            ]
        )
    if current > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Распустить всех (−{current})",
                    callback_data=disband_confirm_callback(fid, 0),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data=menu_callback(fid),
            )
        ]
    )
    rows.append(menu_row(fid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def raid_confirm_kb(
    fief_id: int, *, show_truce: bool = False, open_truce: bool = False
) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=f"radok:{fid}",
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=pending_cancel_callback(fid),
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
                    callback_data=f"radtruce:{fid}",
                )
            ]
        )
    rows.append(menu_row(fid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cover_confirm_kb(fief_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=f"covok:{fid}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=pending_cancel_callback(fid),
                ),
            ],
            menu_row(fid),
        ]
    )


def raid_cancel_intent_kb(fief_id: int, intent_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Снять заявку",
                    callback_data=f"radx:{fid}:{int(intent_id)}",
                )
            ],
            menu_row(fid),
        ]
    )


def caravan_cancel_intent_kb(fief_id: int, intent_id: int) -> InlineKeyboardMarkup:
    return transfer_cancel_intent_kb(fief_id, intent_id)


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
    return with_menu_footer(rows, fief_id)


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
    return with_menu_footer(rows, fief_id)


def gather_resources_kb(
    fief_id: int, *, hungry: bool = False
) -> InlineKeyboardMarkup:
    from app.domain.resource_registry import resource_defs

    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    for rdef in resource_defs():
        if hungry and rdef.key == B.RES_MIGHT:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{rdef.name_ru} +{B.gather_amount(rdef.key)}",
                    callback_data=f"gth:{fid}:{rdef.key}",
                )
            ]
        )
    return with_menu_footer(rows, fid)


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
            [
                InlineKeyboardButton(
                    text="Нечего сносить",
                    callback_data=menu_callback(fief_id),
                )
            ]
        )
    return with_menu_footer(rows, fief_id)


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
    rows.append(
        [
            InlineKeyboardButton(
                text="< Назад",
                callback_data=f"bld:{fief_id}",
            ),
            menu_button(fief_id),
        ]
    )
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
    return with_menu_footer(rows, fief_id)


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
                InlineKeyboardButton(
                    text="Застава",
                    callback_data=f"pct:zst:{fief_id}",
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton(text="Выйти из пакта", callback_data=f"pct:leave:{fief_id}")]
        )
    return with_menu_footer(rows, fief_id)


def cover_stance_kb(fief_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Стоять в стороне",
                    callback_data=f"pct:zsd:{fid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Любого союзника",
                    callback_data=f"pct:zsa:{fid}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Конкретного союзника",
                    callback_data=f"pct:zss:{fid}",
                )
            ],
            [
                InlineKeyboardButton(text="< Назад", callback_data=f"pct:{fid}"),
                menu_button(fid),
            ],
        ]
    )


def cover_ally_pick_kb(
    fief_id: int, allies: list[tuple[int, str]]
) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    for ally_id, label in allies:
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:40],
                    callback_data=f"pct:zstt:{fid}:{int(ally_id)}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="< Назад", callback_data=f"pct:zst:{fid}"),
            menu_button(fid),
        ]
    )
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


# Совместимость: chrome helpers, которые раньше жили здесь.
__all__ = [
    "building_types_kb",
    "build_tiles_kb",
    "caravan_cancel_intent_kb",
    "claimable_kb",
    "cover_ally_pick_kb",
    "cover_confirm_kb",
    "cover_stance_kb",
    "demolish_tiles_kb",
    "disband_confirm_callback",
    "disband_militia_kb",
    "gather_resources_kb",
    "pact_invite_kb",
    "pact_kb",
    "patrol_confirm_callback",
    "patrol_confirm_kb",
    "pending_cancel_callback",
    "pending_cancel_kb",
    "raid_cancel_intent_kb",
    "raid_confirm_kb",
    "raid_targets_kb",
    "realm_picker_kb",
    "starter_tiles_kb",
]
