"""Сборка шагов UI передачи (без хендлерной логики)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from app import balance as B
from app.domain.resource_bags import stash_amount
from app.domain.resource_format import resource_name_ru
from app.ui.flows import send_find_offer, send_offer
from app.ui.keyboards.transfer import (
    transfer_amount_kb,
    transfer_confirm_kb,
    transfer_confirm_summary,
    transfer_custom_amount_kb,
    transfer_resource_kb,
)


def transfer_entry(engine, fief: dict) -> tuple[str, InlineKeyboardMarkup]:
    contacts = engine.list_transfer_contacts(int(fief["id"]))
    return send_offer(int(fief["id"]), contacts)


def transfer_find_prompt(fief_id: int) -> tuple[str, InlineKeyboardMarkup]:
    return send_find_offer(int(fief_id))


def transfer_resource_step(
    engine, fief: dict, target_fief_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    target = engine.fief_by_id(int(target_fief_id))
    label = engine.fief_label(target) if target else str(target_fief_id)
    grain = stash_amount(fief, B.RES_GRAIN)
    goods = stash_amount(fief, B.RES_GOODS)
    text = (
        f"Получатель: <b>{label}</b>\n"
        f"Что отправить? (есть {grain} зерна, {goods} товаров)"
    )
    return text, transfer_resource_kb(
        int(fief["id"]), grain=grain, goods=goods
    )


def transfer_amount_step(
    engine, fief: dict, *, target_fief_id: int, res: str
) -> tuple[str, InlineKeyboardMarkup]:
    target = engine.fief_by_id(int(target_fief_id))
    label = engine.fief_label(target) if target else str(target_fief_id)
    have = stash_amount(fief, res)
    text = (
        f"Получатель: <b>{label}</b>\n"
        f"Сколько {resource_name_ru(res)}? (есть {have})"
    )
    return text, transfer_amount_kb(int(fief["id"]), have=have)


def transfer_custom_amount_step(
    fief_id: int, *, res: str, have: int
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"Напишите число ({resource_name_ru(res)}, макс. {have}).\n"
        "Или нажмите Отмена."
    )
    return text, transfer_custom_amount_kb(int(fief_id))


def transfer_confirm_step(
    engine,
    fief: dict,
    *,
    target_fief_id: int,
    res: str,
    amt: int,
) -> tuple[str, InlineKeyboardMarkup]:
    target = engine.fief_by_id(int(target_fief_id))
    label = engine.fief_label(target) if target else str(target_fief_id)
    world = engine.world(engine.world_id_for_realm(int(fief["realm_id"]))) or {}
    lock_text = engine.format_raid_deadline(world, midpoint=True)
    resolve_text = engine.format_raid_deadline(world, midpoint=False)
    text = transfer_confirm_summary(
        receiver_label=label,
        res=res,
        amt=amt,
        lock_text=lock_text,
        resolve_text=resolve_text,
    )
    return text, transfer_confirm_kb(int(fief["id"]))
