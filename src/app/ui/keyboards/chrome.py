"""Общий chrome DM-клавиатур: < Меню и футер отмены."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def menu_callback(fief_id: int) -> str:
    return f"home:{int(fief_id)}"


def pending_cancel_callback(fief_id: int) -> str:
    return f"pend:cancel:{int(fief_id)}"


def menu_button(fief_id: int) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="< Меню",
        callback_data=menu_callback(fief_id),
    )


def menu_row(fief_id: int) -> list[InlineKeyboardButton]:
    return [menu_button(fief_id)]


def pending_escape_row(fief_id: int) -> list[InlineKeyboardButton]:
    """Отмена (сброс pending) + выход в меню."""
    fid = int(fief_id)
    return [
        InlineKeyboardButton(
            text="Отмена",
            callback_data=pending_cancel_callback(fid),
        ),
        menu_button(fid),
    ]


def with_menu_footer(
    rows: list[list[InlineKeyboardButton]],
    fief_id: int,
) -> InlineKeyboardMarkup:
    out = list(rows)
    out.append(menu_row(fief_id))
    return InlineKeyboardMarkup(inline_keyboard=out)


def with_pending_footer(
    rows: list[list[InlineKeyboardButton]],
    fief_id: int,
) -> InlineKeyboardMarkup:
    out = list(rows)
    out.append(pending_escape_row(fief_id))
    return InlineKeyboardMarkup(inline_keyboard=out)


def menu_only_kb(fief_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[menu_row(fief_id)])
