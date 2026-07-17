"""Тесты баланса, карты, тика, набегов."""
from __future__ import annotations

import math
import random

from app import balance as B
from app.domain.economy import TileView, fief_daily_production
from app.domain.map_gen import generate_map
from random import Random

from app.domain.raids import (
    loot_amounts,
    loot_overkill_factor,
    resolve_raid,
    standing_raid_defense,
)
from app.domain.tick import FiefTickState, apply_fief_tick, collect_pending_bags


def test_militia_upkeep():
    assert B.militia_upkeep_grain(5) == 0
    assert B.militia_upkeep_grain(6) == 1
    assert B.militia_upkeep_grain(14) == 5
    assert B.militia_upkeep_grain(30) == 13


def test_land_upkeep():
    assert B.land_upkeep(1) == 4
    assert B.land_upkeep(2) == 6
    assert B.land_upkeep(9) == 20


def test_claim_costs():
    assert B.claim_cost(2) == 20
    assert B.claim_cost(2, is_wilds=True) == 40


def test_map_gen_has_road_and_river():
    tiles = generate_map(5, 4, random.Random(42))
    assert len(tiles) == 20
    types = {t.tile_type for t in tiles}
    assert B.TILE_ROAD in types
    assert B.TILE_RIVER in types
    assert any(t.is_bridge for t in tiles)


def test_farm_on_field_bonus():
    tiles = [
        TileView(0, 0, B.TILE_FIELD, 1, B.BLD_FARM, 1),
    ]
    prod = fief_daily_production(tiles)
    assert prod.resources()[B.RES_GRAIN] == B.FARM_YIELD[1] * B.NATIVE_BONUS
    assert prod.resources()[B.RES_GOODS] == B.FIEF_BASE_GOODS


def test_fief_base_goods_income():
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    prod = fief_daily_production(tiles)
    assert prod.resources()[B.RES_GOODS] == B.FIEF_BASE_GOODS
    hungry = fief_daily_production(tiles, hungry=True)
    assert (
        hungry.resources()[B.RES_GOODS]
        == B.FIEF_BASE_GOODS * B.HUNGER_PRODUCTION_MULT
    )
    overgrown = [TileView(0, 0, B.TILE_HILLS, 1, None, 0, is_overgrown=True)]
    assert fief_daily_production(overgrown).resources()[B.RES_GOODS] == 0


def _tick_state(**stash_pending) -> FiefTickState:
    return FiefTickState(
        stash={
            B.RES_GRAIN: stash_pending.get("grain", 0),
            B.RES_GOODS: stash_pending.get("goods", 0),
            B.RES_MIGHT: stash_pending.get("might", 0),
        },
        pending={
            B.RES_GRAIN: float(stash_pending.get("pending_grain", 0)),
            B.RES_GOODS: float(stash_pending.get("pending_goods", 0)),
            B.RES_MIGHT: float(stash_pending.get("pending_might", 0)),
        },
        actions=stash_pending["actions"],
        hungry=stash_pending.get("hungry", False),
        tiles=stash_pending["tiles"],
        barn_level=stash_pending.get("barn_level", 0),
    )


def _loot_gd(ratio, ug, ud, dg, dd, rng=None):
    bag = loot_amounts(
        ratio,
        {B.RES_GRAIN: ug, B.RES_GOODS: ud},
        {B.RES_GRAIN: float(dg), B.RES_GOODS: float(dd)},
        rng=rng,
    )
    return bag[B.RES_GRAIN], bag[B.RES_GOODS]


def _resolve_gd(*, grain: int, goods: int, daily_g: float, daily_d: float, **kwargs):
    return resolve_raid(
        victim_stash={B.RES_GRAIN: grain, B.RES_GOODS: goods, B.RES_MIGHT: 0},
        victim_daily={B.RES_GRAIN: daily_g, B.RES_GOODS: daily_d, B.RES_MIGHT: 0.0},
        **kwargs,
    )


def test_tick_militia_disband():
    # Без производства: земля и дружина не оплачиваются из pending
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    state = _tick_state(
        grain=0, goods=0, might=30, actions=1, tiles=tiles, barn_level=0
    )
    out = apply_fief_tick(state)
    assert out.hungry is True
    assert out.stash[B.RES_MIGHT] <= B.MILITIA_FREE
    assert out.militia_disbanded >= 20


def test_tick_pays_land_and_grants_action():
    tiles = [TileView(0, 0, B.TILE_FIELD, 1, B.BLD_FARM, 1)]
    state = _tick_state(
        grain=50, goods=10, might=5, actions=1, tiles=tiles, barn_level=0
    )
    out = apply_fief_tick(state)
    assert out.hungry is False
    assert out.actions == 2
    assert out.stash[B.RES_GRAIN] == 50 - out.land_upkeep - out.militia_upkeep


def test_raid_fail_loses_all_might():
    r = _resolve_gd(
        attacker_name="A",
        victim_name="B",
        attack_might=5,
        watch_defense=40,
        patrol_active=True,
        intercept=True,
        grain=100,
        goods=100,
        barn_level=0,
        daily_g=10,
        daily_d=10,
    )
    assert r.success is False
    assert r.might_lost == 5


def test_raid_loot_caps():
    g, d = _loot_gd(0.9, 100, 100, 5, 5, rng=Random(0))
    assert g + d <= int(0.40 * 200)  # max stash frac
    assert g + d <= int(3 * (5 + 5))  # max days of prod


def test_loot_overkill_factor_edge_vs_crush():
    assert loot_overkill_factor(B.RAID_SUCCESS_R) == B.RAID_LOOT_EDGE_FACTOR
    assert loot_overkill_factor(B.RAID_LOOT_OVERKILL_R) == 1.0
    assert loot_overkill_factor(0.99) == 1.0
    mid = (B.RAID_SUCCESS_R + B.RAID_LOOT_OVERKILL_R) / 2
    assert B.RAID_LOOT_EDGE_FACTOR < loot_overkill_factor(mid) < 1.0


def test_loot_amounts_edge_thinner_than_overkill():
    # Один и тот же rng-свинг: у порога добыча заметно меньше, чем при перевесе.
    edge_g, edge_d = _loot_gd(
        B.RAID_SUCCESS_R, 200, 200, 50, 50, rng=Random(1)
    )
    crush_g, crush_d = _loot_gd(0.75, 200, 200, 50, 50, rng=Random(1))
    assert edge_g + edge_d > 0
    assert crush_g + crush_d > edge_g + edge_d


class _FixedLootSwing:
    def __init__(self, value: float) -> None:
        self.value = value

    def uniform(self, _a: float, _b: float) -> float:
        return self.value


def test_loot_amounts_swing_moves_haul():
    low_g, low_d = _loot_gd(
        0.75, 200, 200, 50, 50, rng=_FixedLootSwing(B.RAID_LOOT_RND_MIN)
    )
    high_g, high_d = _loot_gd(
        0.75, 200, 200, 50, 50, rng=_FixedLootSwing(B.RAID_LOOT_RND_MAX)
    )
    assert high_g + high_d > low_g + low_d


def test_loot_amounts_edge_small_stash_not_empty():
    g, d = _loot_gd(
        B.RAID_SUCCESS_R,
        8,
        8,
        50,
        50,
        rng=_FixedLootSwing(B.RAID_LOOT_RND_MIN),
    )
    assert g + d >= 1
    assert g + d <= 16


def test_raid_success_might_loss_severe():
    r = _resolve_gd(
        attacker_name="A",
        victim_name="B",
        attack_might=8,
        watch_defense=0,
        patrol_active=False,
        intercept=False,
        grain=200,
        goods=200,
        barn_level=0,
        daily_g=20,
        daily_d=20,
        rng=Random(0),
    )
    assert r.success is True
    assert r.might_lost == max(1, int(round(8 * B.RAID_SUCCESS_MIGHT_LOSS_FRAC)))
    assert sum(r.stolen.values()) > 0
    assert r.public_line == "A ограбил B"
    assert "зерна" not in r.public_line
    assert "товаров" not in r.public_line


def test_raid_defense_includes_victim_might():
    # 8 vs watch 0 succeeds; same 8 vs stockpile 40 fails (ratio < 0.33).
    soft = _resolve_gd(
        attacker_name="A",
        victim_name="B",
        attack_might=8,
        watch_defense=0,
        patrol_active=False,
        intercept=False,
        grain=200,
        goods=200,
        barn_level=0,
        daily_g=20,
        daily_d=20,
        victim_might=0,
    )
    hard = _resolve_gd(
        attacker_name="A",
        victim_name="B",
        attack_might=8,
        watch_defense=0,
        patrol_active=False,
        intercept=False,
        grain=200,
        goods=200,
        barn_level=0,
        daily_g=20,
        daily_d=20,
        victim_might=40,
    )
    assert soft.success is True
    assert hard.success is False
    assert hard.defense_used == 40
    assert hard.might_lost == 8


def test_raid_defense_stacks_watch_and_victim_might():
    r = _resolve_gd(
        attacker_name="A",
        victim_name="B",
        attack_might=10,
        watch_defense=20,
        patrol_active=False,
        intercept=False,
        grain=100,
        goods=100,
        barn_level=0,
        daily_g=10,
        daily_d=10,
        victim_might=15,
    )
    assert r.defense_used == 35


def test_standing_raid_defense_matches_raid_stack():
    assert standing_raid_defense(
        watch_defense=12,
        victim_might=5,
        patrol_active=False,
    ) == 17
    assert standing_raid_defense(
        watch_defense=12,
        victim_might=5,
        patrol_active=True,
    ) == 17 + B.PATROL_DEFENSE_BONUS
    assert standing_raid_defense(
        watch_defense=12,
        victim_might=5,
        patrol_active=True,
        fog_ignores_patrol=True,
    ) == 17


def test_collect_respects_cap():
    stash, pending, _notes = collect_pending_bags(
        {B.RES_GRAIN: 140, B.RES_GOODS: 140, B.RES_MIGHT: 0},
        {B.RES_GRAIN: 50.0, B.RES_GOODS: 50.0, B.RES_MIGHT: 10.0},
        0,
    )
    assert stash[B.RES_GRAIN] == B.DEFAULT_STASH_CAP
    assert stash[B.RES_GOODS] == B.DEFAULT_STASH_CAP
    assert stash[B.RES_MIGHT] == 10
    assert pending[B.RES_GRAIN] == 0.0
    assert pending[B.RES_GOODS] == 0.0
    assert pending[B.RES_MIGHT] == 0.0


def test_collect_pending_can_skip_might():
    stash, pending, _notes = collect_pending_bags(
        {B.RES_GRAIN: 10, B.RES_GOODS: 10, B.RES_MIGHT: 7},
        {B.RES_GRAIN: 20.0, B.RES_GOODS: 15.0, B.RES_MIGHT: 8.0},
        0,
        include_might=False,
    )
    assert stash[B.RES_GRAIN] == 30
    assert stash[B.RES_GOODS] == 25
    assert stash[B.RES_MIGHT] == 7
    assert pending[B.RES_GRAIN] == 0.0
    assert pending[B.RES_GOODS] == 0.0
    assert pending[B.RES_MIGHT] == 8.0


def test_best_rectangle_min():
    w, h = B.best_rectangle(B.MAP_MIN_TILES)
    assert (w, h) == (6, 6)
    assert w * h >= B.MAP_MIN_TILES
    assert w * h <= B.MAP_MAX_TILES


def test_map_target_tiles_floor():
    assert B.map_target_tiles(1) == B.MAP_MIN_TILES
    assert B.map_target_tiles(4) == B.MAP_MIN_TILES
    assert B.map_target_tiles(8) >= 72
    assert B.map_target_tiles(100) == B.MAP_MAX_TILES
