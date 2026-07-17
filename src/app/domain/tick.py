"""Чистое разрешение дневного тика по одной усадьбе (без БД)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app import balance as B
from app.domain.economy import Production, TileView, fief_daily_production
from app.domain.resources import (
    LIVE_RESOURCES,
    PENDING_COLUMN,
    PendingBag,
    ResourceBag,
    STASH_CAPPED_RESOURCES,
    UNCAPPED_RESOURCES,
    apply_production_to_pending,
    fief_balance_columns,
    pending_from_row,
    stash_columns,
    stash_from_row,
)


@dataclass
class FiefTickState:
    grain: int
    goods: int
    might: int
    pending_grain: float
    pending_goods: float
    pending_might: float
    actions: int
    hungry: bool
    tiles: list[TileView]
    barn_level: int
    farm_mult: float = 1.0
    workshop_mult: float = 1.0

    @classmethod
    def from_fief_row(
        cls,
        fief: Mapping[str, Any],
        tiles: list[TileView],
        barn_level: int,
        *,
        farm_mult: float = 1.0,
        workshop_mult: float = 1.0,
    ) -> "FiefTickState":
        stash = stash_from_row(fief)
        pending = pending_from_row(fief)
        return cls(
            grain=stash[B.RES_GRAIN],
            goods=stash[B.RES_GOODS],
            might=stash[B.RES_MIGHT],
            pending_grain=pending[B.RES_GRAIN],
            pending_goods=pending[B.RES_GOODS],
            pending_might=pending[B.RES_MIGHT],
            actions=int(fief["actions"]),
            hungry=bool(fief["hungry"]),
            tiles=tiles,
            barn_level=barn_level,
            farm_mult=farm_mult,
            workshop_mult=workshop_mult,
        )

    def stash_bag(self) -> ResourceBag:
        return {key: int(getattr(self, key)) for key in LIVE_RESOURCES}

    def pending_bag(self) -> PendingBag:
        return {
            key: float(getattr(self, PENDING_COLUMN[key])) for key in LIVE_RESOURCES
        }


@dataclass
class TickOutcome:
    grain: int
    goods: int
    might: int
    pending_grain: float
    pending_goods: float
    pending_might: float
    actions: int
    hungry: bool
    land_upkeep: int
    militia_upkeep: int
    militia_disbanded: int
    production: Production
    notes: list[str] = field(default_factory=list)

    def balance_columns(self) -> dict[str, int | float]:
        return fief_balance_columns(
            {key: int(getattr(self, key)) for key in LIVE_RESOURCES},
            {
                key: float(getattr(self, PENDING_COLUMN[key]))
                for key in LIVE_RESOURCES
            },
        )


def _pay_grain(grain: int, pending: float, amount: int) -> tuple[int, float, int]:
    """Списывает amount зерна со stash, затем pending. Возвращает (grain, pending, paid)."""
    need = max(0, amount)
    take = min(grain, need)
    grain -= take
    need -= take
    if need > 0 and pending > 0:
        take_p = min(pending, float(need))
        pending -= take_p
        need -= int(take_p)
        frac = take_p - int(take_p)
        if need > 0 and frac > 0:
            pending = max(0.0, pending - (need - frac))
            need = 0
        elif need > 0 and pending >= need:
            pending -= need
            need = 0
    paid = amount - need
    return grain, max(0.0, pending), paid


def apply_fief_tick(state: FiefTickState) -> TickOutcome:
    notes: list[str] = []
    tiles_count = sum(1 for t in state.tiles if not t.is_overgrown)
    prod = fief_daily_production(
        state.tiles,
        hungry=state.hungry,
        farm_mult=state.farm_mult,
        current_might=int(state.might),
    )
    if state.workshop_mult != 1.0:
        prod = replace(prod, goods=prod.goods * state.workshop_mult)

    cap_days = B.collect_cap_days(state.barn_level)
    prod_bag = prod.resources()
    pending = state.pending_bag()

    max_by_res = {
        key: (
            prod_bag[key] * cap_days
            if prod_bag[key] > 0
            else pending[key]
        )
        for key in LIVE_RESOURCES
    }
    if prod_bag[B.RES_GRAIN] > 0 and pending[B.RES_GRAIN] + prod_bag[B.RES_GRAIN] > max_by_res[
        B.RES_GRAIN
    ] + 1e-6:
        notes.append("Крысы съели часть неубранного зерна")
    if prod_bag[B.RES_GOODS] > 0 and pending[B.RES_GOODS] + prod_bag[B.RES_GOODS] > max_by_res[
        B.RES_GOODS
    ] + 1e-6:
        notes.append("Неубранные товары испортились")

    pending = apply_production_to_pending(pending, prod_bag, cap_days)

    grain = state.grain
    goods = state.goods
    might = state.might
    pending_grain = pending[B.RES_GRAIN]
    pending_goods = pending[B.RES_GOODS]
    pending_might = pending[B.RES_MIGHT]

    land = B.land_upkeep(max(1, tiles_count))
    grain, pending_grain, paid_land = _pay_grain(grain, pending_grain, land)
    hungry = paid_land < land
    if hungry:
        notes.append("Голод: нечем платить содержание земли")

    militia_need = B.militia_upkeep_grain(might)
    grain, pending_grain, paid_mil = _pay_grain(grain, pending_grain, militia_need)
    disbanded = 0
    if paid_mil < militia_need:
        keep = min(might, B.militia_affordable(might, paid_mil))
        if paid_mil <= 0:
            keep = min(might, B.MILITIA_FREE)
        disbanded = max(0, might - keep)
        might = keep
        if disbanded:
            notes.append(f"Нечем кормить дружину - разошлись {disbanded} (−{disbanded} Силы)")

    # Голод снимается только после полного тика с оплаченным land upkeep
    # (если сейчас оплатили - hungry False)
    actions = min(B.ACTIONS_BANK_MAX, state.actions + B.ACTIONS_PER_DAY)

    return TickOutcome(
        grain=grain,
        goods=goods,
        might=might,
        pending_grain=pending_grain,
        pending_goods=pending_goods,
        pending_might=pending_might,
        actions=actions,
        hungry=hungry,
        land_upkeep=land,
        militia_upkeep=militia_need,
        militia_disbanded=disbanded,
        production=prod,
        notes=notes,
    )


def collect_pending_bags(
    stash: Mapping[str, int],
    pending: Mapping[str, float],
    barn_level: int,
    *,
    include_might: bool = True,
) -> tuple[ResourceBag, PendingBag, list[str]]:
    notes: list[str] = []
    cap = B.stash_cap(barn_level)
    out_stash = stash_columns(stash)
    out_pending = {
        key: float(pending.get(key, 0) or 0) for key in LIVE_RESOURCES
    }
    truncated = False
    for key in STASH_CAPPED_RESOURCES:
        add = int(out_pending[key])
        room = max(0, cap - out_stash[key])
        take = min(add, room)
        if take < add:
            truncated = True
        out_stash[key] += take
        out_pending[key] = 0.0
    if truncated:
        notes.append("Склад полон - часть урожая не вошла")
    for key in UNCAPPED_RESOURCES:
        if key == B.RES_MIGHT and not include_might:
            continue
        out_stash[key] += int(out_pending[key])
        out_pending[key] = 0.0
    return out_stash, out_pending, notes


def collect_pending(
    grain: int,
    goods: int,
    might: int,
    pending_grain: float,
    pending_goods: float,
    pending_might: float,
    barn_level: int,
    *,
    include_might: bool = True,
) -> tuple[int, int, int, float, float, float, list[str]]:
    stash, pending, notes = collect_pending_bags(
        {
            B.RES_GRAIN: grain,
            B.RES_GOODS: goods,
            B.RES_MIGHT: might,
        },
        {
            B.RES_GRAIN: pending_grain,
            B.RES_GOODS: pending_goods,
            B.RES_MIGHT: pending_might,
        },
        barn_level,
        include_might=include_might,
    )
    return (
        stash[B.RES_GRAIN],
        stash[B.RES_GOODS],
        stash[B.RES_MIGHT],
        pending[B.RES_GRAIN],
        pending[B.RES_GOODS],
        pending[B.RES_MIGHT],
        notes,
    )
