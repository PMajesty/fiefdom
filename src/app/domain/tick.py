"""Чистое разрешение дневного тика по одной усадьбе (без БД)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app import balance as B
from app.domain.production import Production, TileView, fief_daily_production

from app.domain.resources import (
    PendingBag,
    ResourceBag,
    apply_production_to_pending,
    fief_balance_columns,
    live_resource_keys,
    pending_from_row,
    stash_capped_keys,
    stash_columns,
    stash_from_row,
    uncapped_keys,
)


@dataclass
class FiefTickState:
    stash: ResourceBag
    pending: PendingBag
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
        return cls(
            stash=stash_from_row(fief),
            pending=pending_from_row(fief),
            actions=int(fief["actions"]),
            hungry=bool(fief["hungry"]),
            tiles=tiles,
            barn_level=barn_level,
            farm_mult=farm_mult,
            workshop_mult=workshop_mult,
        )

    def stash_bag(self) -> ResourceBag:
        return dict(self.stash)

    def pending_bag(self) -> PendingBag:
        return dict(self.pending)


@dataclass
class TickOutcome:
    stash: ResourceBag
    pending: PendingBag
    actions: int
    hungry: bool
    land_upkeep: int
    militia_upkeep: int
    militia_disbanded: int
    production: Production
    notes: list[str] = field(default_factory=list)

    def balance_columns(self) -> dict[str, int | float]:
        return fief_balance_columns(self.stash, self.pending)


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
        current_might=int(state.stash[B.RES_MIGHT]),
    )
    if state.workshop_mult != 1.0:
        prod = prod.with_amounts(
            **{B.RES_GOODS: prod.resources()[B.RES_GOODS] * state.workshop_mult}
        )

    cap_days = B.collect_cap_days(state.barn_level)
    prod_bag = prod.resources()
    pending = state.pending_bag()

    max_by_res = {
        key: (
            prod_bag[key] * cap_days
            if prod_bag[key] > 0
            else pending[key]
        )
        for key in live_resource_keys()
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

    stash = state.stash_bag()
    grain = stash[B.RES_GRAIN]
    might = stash[B.RES_MIGHT]
    pending_grain = pending[B.RES_GRAIN]

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

    stash[B.RES_GRAIN] = grain
    stash[B.RES_MIGHT] = might
    pending[B.RES_GRAIN] = pending_grain

    actions = min(B.ACTIONS_BANK_MAX, state.actions + B.ACTIONS_PER_DAY)

    return TickOutcome(
        stash=stash_columns(stash),
        pending={key: float(pending.get(key, 0) or 0) for key in live_resource_keys()},
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
        key: float(pending.get(key, 0) or 0) for key in live_resource_keys()
    }
    truncated = False
    for key in stash_capped_keys():
        add = int(out_pending[key])
        room = max(0, cap - out_stash[key])
        take = min(add, room)
        if take < add:
            truncated = True
        out_stash[key] += take
        out_pending[key] = 0.0
    if truncated:
        notes.append("Склад полон - часть урожая не вошла")
    for key in uncapped_keys():
        if key == B.RES_MIGHT and not include_might:
            continue
        out_stash[key] += int(out_pending[key])
        out_pending[key] = 0.0
    return out_stash, out_pending, notes
