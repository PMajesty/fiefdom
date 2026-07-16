"""Чистое разрешение дневного тика по одной усадьбе (без БД)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app import balance as B
from app.domain.economy import Production, TileView, fief_daily_production


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
        prod = Production(
            grain=prod.grain,
            goods=prod.goods * state.workshop_mult,
            might=prod.might,
            defense=prod.defense,
        )

    cap_days = B.collect_cap_days(state.barn_level)
    max_pg = prod.grain * cap_days if prod.grain > 0 else state.pending_grain
    max_pd = prod.goods * cap_days if prod.goods > 0 else state.pending_goods
    max_pm = prod.might * cap_days if prod.might > 0 else state.pending_might

    if prod.grain > 0 and state.pending_grain + prod.grain > max_pg + 1e-6:
        notes.append("Крысы съели часть неубранного зерна")
    if prod.goods > 0 and state.pending_goods + prod.goods > max_pd + 1e-6:
        notes.append("Неубранные товары испортились")

    pending_grain = min(max_pg, state.pending_grain + prod.grain) if prod.grain > 0 else min(
        state.pending_grain, max_pg if max_pg else state.pending_grain
    )
    pending_goods = min(max_pd, state.pending_goods + prod.goods) if prod.goods > 0 else state.pending_goods
    pending_might = min(max_pm, state.pending_might + prod.might) if prod.might > 0 else state.pending_might

    # если производства нет - pending не растёт, но кап по старым дням не жмём агрессивно
    if prod.grain > 0:
        pending_grain = min(prod.grain * cap_days, state.pending_grain + prod.grain)
    else:
        pending_grain = state.pending_grain
    if prod.goods > 0:
        pending_goods = min(prod.goods * cap_days, state.pending_goods + prod.goods)
    else:
        pending_goods = state.pending_goods
    if prod.might > 0:
        pending_might = min(prod.might * cap_days, state.pending_might + prod.might)
    else:
        pending_might = state.pending_might

    grain = state.grain
    goods = state.goods
    might = state.might

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
    notes: list[str] = []
    cap = B.stash_cap(barn_level)
    g_add = int(pending_grain)
    d_add = int(pending_goods)
    room_g = max(0, cap - grain)
    room_d = max(0, cap - goods)
    take_g = min(g_add, room_g)
    take_d = min(d_add, room_d)
    if take_g < g_add or take_d < d_add:
        notes.append("Склад полон - часть урожая не вошла")
    grain += take_g
    goods += take_d
    if include_might:
        might += int(pending_might)
        pending_might = 0.0
    return grain, goods, might, 0.0, 0.0, pending_might, notes
