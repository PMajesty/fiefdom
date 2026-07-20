"""Offer/menu flow views: (text, keyboard) for DM and callback entry points."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app.ui.keyboards import (
    claimable_kb,
    pact_kb,
    raid_targets_kb,
)
from app.ui.keyboards.chrome import menu_only_kb
from app.ui.keyboards.transfer import transfer_contacts_kb


def claim_offer(
    fief_id: int,
    claimable: list[tuple[int, int]],
    *,
    next_tile_count: int,
    tile_meta: dict[tuple[int, int], tuple[str, bool]],
    empty_text: str,
    prompt_text: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    if not claimable:
        return empty_text, menu_only_kb(fief_id)
    return (
        prompt_text,
        claimable_kb(
            fief_id,
            claimable,
            next_tile_count=next_tile_count,
            tile_meta=tile_meta,
        ),
    )


def raid_targets_offer(
    fief_id: int,
    targets: list[dict],
    *,
    empty_text: str,
    prompt_text: str,
) -> tuple[str, InlineKeyboardMarkup | None]:
    if not targets:
        return empty_text, menu_only_kb(fief_id)
    return prompt_text, raid_targets_kb(fief_id, targets)


def send_offer(
    fief_id: int,
    contacts: list[tuple[int, str]] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Старт передачи: короткий prompt + контакты / Найти…"""
    text = (
        "Кому отправить зерно или товары?\n"
        "Выберите получателя или нажмите \"Найти…\"."
    )
    return text, transfer_contacts_kb(fief_id, contacts or [])


def send_find_offer(fief_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from app.ui.keyboards import pending_cancel_kb

    text = (
        "Напишите id усадьбы, имя или @username.\n"
        "Или нажмите Отмена."
    )
    return text, pending_cancel_kb(fief_id)


def pact_menu_offer(
    fief_id: int,
    *,
    in_pact: bool,
    is_founder: bool,
    text: str,
) -> tuple[str, InlineKeyboardMarkup]:
    return text, pact_kb(fief_id, in_pact, is_founder)
