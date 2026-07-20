"""Offer/menu flow views: (text, keyboard) for DM and callback entry points."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app import balance as B
from app.ui.keyboards import (
    claimable_kb,
    pact_kb,
    pending_cancel_kb,
    raid_targets_kb,
)


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
        return empty_text, None
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
        return empty_text, None
    return prompt_text, raid_targets_kb(fief_id, targets)


def send_offer(fief_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        "Куда отправить обоз с зерном или товарами?\n"
        "Напишите id усадьбы, имя или @username.\n"
        "Объявить можно в первой половине окна тика (как набег); "
        "вернуть - до середины окна. Доставка после колокола тика. "
        f"От {B.CARAVAN_PUBLIC_AMOUNT} и больше долина увидит выезд; "
        "мелкое - только адресату. Силу везти нельзя.\n"
        "Или напишите \"отмена\"."
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
