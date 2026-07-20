"""Чистая алгебра сумок ресурсов: stash / pending / loot."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.resource_registry import (
    fief_balance_columns,
    live_resource_keys,
    pending_column_for,
    raid_lootable_keys,
    stash_columns,
    uncapped_keys,
)


ResourceBag = dict[str, int]
PendingBag = dict[str, float]
LootBag = dict[str, int]


def empty_stash() -> ResourceBag:
    return {key: 0 for key in live_resource_keys()}


def empty_pending() -> PendingBag:
    return {key: 0.0 for key in live_resource_keys()}


def empty_loot_bag() -> LootBag:
    return {key: 0 for key in raid_lootable_keys()}


def stash_from_row(row: Mapping[str, Any]) -> ResourceBag:
    return {key: int(row.get(key, 0) or 0) for key in live_resource_keys()}


def pending_from_row(row: Mapping[str, Any]) -> PendingBag:
    return {
        key: float(row.get(pending_column_for(key), 0) or 0)
        for key in live_resource_keys()
    }


def migrate_row_balances(row: Mapping[str, Any]) -> dict[str, int | float]:
    """Идемпотентный round-trip колонки → bag → колонки без смены значений."""
    return fief_balance_columns(stash_from_row(row), pending_from_row(row))


def add_bags(

    left: Mapping[str, float], right: Mapping[str, float]
) -> dict[str, float]:
    return {
        key: float(left.get(key, 0) or 0) + float(right.get(key, 0) or 0)
        for key in live_resource_keys()
    }


def scale_bag(bag: Mapping[str, float], mult: float) -> dict[str, float]:
    return {
        key: float(bag.get(key, 0) or 0) * mult for key in live_resource_keys()
    }


def capped_pending_add(current: float, produced: float, cap_days: int) -> float:
    """Кап неубранного: как в apply_fief_tick при produced > 0."""
    if produced > 0:
        return min(produced * cap_days, current + produced)
    return current


def apply_production_to_pending(
    pending: Mapping[str, float],
    production: Mapping[str, float],
    cap_days: int,
) -> PendingBag:
    return {
        key: capped_pending_add(
            float(pending.get(key, 0) or 0),
            float(production.get(key, 0) or 0),
            cap_days,
        )
        for key in live_resource_keys()
    }


def normalize_debit_amounts(
    amounts: Mapping[str, int] | None = None,
    **kwargs: int,
) -> ResourceBag:
    merged: dict[str, int] = {}
    if amounts:
        for key, raw in amounts.items():
            merged[str(key)] = int(raw)
    for key, raw in kwargs.items():
        merged[str(key)] = int(raw)
    if not merged:
        raise ValueError("debit_fief_resources: пустой списанный набор")
    live = set(live_resource_keys())
    out: ResourceBag = {}
    for key, amt in merged.items():
        if key not in live:
            raise ValueError(f"debit_fief_resources: колонка {key}")
        if amt <= 0:
            raise ValueError("debit_fief_resources: сумма должна быть > 0")
        out[key] = amt
    return out


normalize_credit_amounts = normalize_debit_amounts


def apply_gather_to_stash(
    stash: Mapping[str, int],
    resource: str,
    amount: int,
    *,
    cap: int,
) -> tuple[ResourceBag, int]:
    """Добавляет сбор. При held > cap не обрезает остаток, только room=0."""
    if resource not in live_resource_keys():
        raise ValueError(f"Нельзя собрать: {resource}")
    out = stash_columns(stash)
    qty = int(amount)
    if resource in uncapped_keys():
        out[resource] = out[resource] + qty
        return out, qty
    room = max(0, int(cap) - out[resource])
    gained = min(qty, room)
    out[resource] = out[resource] + gained
    return out, gained


def stash_amount(row: Mapping[str, Any], resource: str) -> int:
    return int(row.get(resource, 0) or 0)


def capped_receive_amount(held: int, amount: int, cap: int) -> int:
    """Сколько можно принять без роста выше cap. held > cap → 0, без обрезки."""
    return min(int(amount), max(0, int(cap) - int(held)))
