"""Клавиатуры мастера передачи ресурсов (кнопки UI, не RP-уведомления)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import balance as B
from app.domain.resource_format import resource_name_ru
from app.ui.keyboards.chrome import (
    menu_button,
    menu_row,
    pending_escape_row,
    with_pending_footer,
)


def transfer_contacts_kb(
    fief_id: int,
    contacts: list[tuple[int, str]],
) -> InlineKeyboardMarkup:
    """contacts: (target_fief_id, label)."""
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    for target_id, label in contacts[:8]:
        rows.append(
            [
                InlineKeyboardButton(
                    text=str(label)[:40],
                    callback_data=f"snd:{fid}:t:{int(target_id)}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Найти…",
                callback_data=f"snd:{fid}:find",
            )
        ]
    )
    rows.append(pending_escape_row(fid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transfer_resource_kb(
    fief_id: int,
    *,
    grain: int,
    goods: int,
) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    rows = [
        [
            InlineKeyboardButton(
                text=f"Зерно ({int(grain)})",
                callback_data=f"snd:{fid}:r:{B.RES_GRAIN}",
            ),
            InlineKeyboardButton(
                text=f"Товары ({int(goods)})",
                callback_data=f"snd:{fid}:r:{B.RES_GOODS}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="< Назад",
                callback_data=f"snd:{fid}",
            ),
        ],
        pending_escape_row(fid),
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transfer_amount_presets(have: int) -> list[int]:
    """Пресеты не больше запаса; 30 = порог публичности."""
    have = max(0, int(have))
    if have <= 0:
        return []
    presets: list[int] = []
    for n in (5, 10, 25, int(B.CARAVAN_PUBLIC_AMOUNT)):
        if 0 < n < have and n not in presets:
            presets.append(n)
    if have not in presets:
        presets.append(have)
    return presets


def transfer_amount_kb(
    fief_id: int,
    *,
    have: int,
) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for amt in transfer_amount_presets(have):
        label = "Всё" if amt == int(have) else str(amt)
        if amt == int(B.CARAVAN_PUBLIC_AMOUNT) and amt != int(have):
            label = f"{amt}*"
        row.append(
            InlineKeyboardButton(
                text=label,
                callback_data=f"snd:{fid}:a:{int(amt)}",
            )
        )
        if len(row) >= 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(
                text="Своё число",
                callback_data=f"snd:{fid}:a:x",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="< Назад",
                callback_data=f"snd:{fid}:back:res",
            ),
        ]
    )
    rows.append(pending_escape_row(fid))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def transfer_confirm_kb(fief_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить",
                    callback_data=f"snd:{fid}:ok",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"pend:cancel:{fid}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="< Назад",
                    callback_data=f"snd:{fid}:back:amt",
                ),
                menu_button(fid),
            ],
        ]
    )


def transfer_cancel_intent_kb(fief_id: int, intent_id: int) -> InlineKeyboardMarkup:
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отменить отправку",
                    callback_data=f"cvx:{fid}:{int(intent_id)}",
                )
            ],
            menu_row(fid),
        ]
    )


def transfer_custom_amount_kb(fief_id: int) -> InlineKeyboardMarkup:
    return with_pending_footer([], fief_id)


def transfer_confirm_summary(
    *,
    receiver_label: str,
    res: str,
    amt: int,
    lock_text: str,
    resolve_text: str,
) -> str:
    res_name = resource_name_ru(res)
    return (
        f"Кому: <b>{receiver_label}</b>\n"
        f"Что: {int(amt)} {res_name}\n"
        f"Вернуть можно до {lock_text}. "
        f"Доставка после колокола около {resolve_text}. "
        f"До середины окна груз видите только вы; потом узнает адресат. "
        f"Несколько обозов одному двору складываются - "
        f"от {int(B.CARAVAN_PUBLIC_AMOUNT)} континент видит выезд."
    )
