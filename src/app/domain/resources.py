"""Сумка ресурсов: data-driven grain/goods/might при колонках БД как источнике истины."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import balance as B

# Единственные живые ресурсы. Новые ключи не вводить без отдельного геймплей-решения.
LIVE_RESOURCES: tuple[str, ...] = (B.RES_GRAIN, B.RES_GOODS, B.RES_MIGHT)

# Политика сбора: TRADEABLE под кап склада; остальное из LIVE - без капа.
STASH_CAPPED_RESOURCES: frozenset[str] = frozenset(B.TRADEABLE)
UNCAPPED_RESOURCES: frozenset[str] = frozenset(LIVE_RESOURCES) - STASH_CAPPED_RESOURCES

PENDING_COLUMN: dict[str, str] = {key: f"pending_{key}" for key in LIVE_RESOURCES}

ResourceBag = dict[str, int]
PendingBag = dict[str, float]


def empty_stash() -> ResourceBag:
    return {key: 0 for key in LIVE_RESOURCES}


def empty_pending() -> PendingBag:
    return {key: 0.0 for key in LIVE_RESOURCES}


def stash_from_row(row: Mapping[str, Any]) -> ResourceBag:
    return {key: int(row.get(key, 0) or 0) for key in LIVE_RESOURCES}


def pending_from_row(row: Mapping[str, Any]) -> PendingBag:
    return {
        key: float(row.get(PENDING_COLUMN[key], 0) or 0) for key in LIVE_RESOURCES
    }


def stash_columns(bag: Mapping[str, int | float]) -> ResourceBag:
    """Bag → kwargs колонок stash. Только live-ключи."""
    return {key: int(bag.get(key, 0) or 0) for key in LIVE_RESOURCES}


def pending_columns(bag: Mapping[str, int | float]) -> dict[str, float]:
    return {
        PENDING_COLUMN[key]: float(bag.get(key, 0) or 0) for key in LIVE_RESOURCES
    }


def fief_balance_columns(
    stash: Mapping[str, int | float],
    pending: Mapping[str, int | float],
) -> dict[str, int | float]:
    out: dict[str, int | float] = dict(stash_columns(stash))
    out.update(pending_columns(pending))
    return out


def migrate_row_balances(row: Mapping[str, Any]) -> dict[str, int | float]:
    """Идемпотентный round-trip колонки → bag → колонки без смены значений."""
    return fief_balance_columns(stash_from_row(row), pending_from_row(row))


def add_bags(
    left: Mapping[str, float], right: Mapping[str, float]
) -> dict[str, float]:
    return {
        key: float(left.get(key, 0) or 0) + float(right.get(key, 0) or 0)
        for key in LIVE_RESOURCES
    }


def scale_bag(bag: Mapping[str, float], mult: float) -> dict[str, float]:
    return {key: float(bag.get(key, 0) or 0) * mult for key in LIVE_RESOURCES}


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
        for key in LIVE_RESOURCES
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
    out: ResourceBag = {}
    for key, amt in merged.items():
        if key not in LIVE_RESOURCES:
            raise ValueError(f"debit_fief_resources: колонка {key}")
        if amt <= 0:
            raise ValueError("debit_fief_resources: сумма должна быть > 0")
        out[key] = amt
    return out


def apply_gather_to_stash(
    stash: Mapping[str, int],
    resource: str,
    amount: int,
    *,
    cap: int,
) -> tuple[ResourceBag, int]:
    if resource not in LIVE_RESOURCES:
        raise ValueError(f"Нельзя собрать: {resource}")
    out = stash_columns(stash)
    qty = int(amount)
    if resource in UNCAPPED_RESOURCES:
        out[resource] = out[resource] + qty
        return out, qty
    room = max(0, int(cap) - out[resource])
    gained = min(qty, room)
    out[resource] = out[resource] + gained
    return out, gained
