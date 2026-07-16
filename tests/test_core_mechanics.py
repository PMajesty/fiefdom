"""Тесты баланса, карты, тика, набегов."""
from __future__ import annotations

import math
import random

from app import balance as B
from app.domain.economy import TileView, fief_daily_production
from app.domain.map_gen import generate_map
from app.domain.raids import loot_amounts, resolve_raid
from app.domain.tick import FiefTickState, apply_fief_tick, collect_pending


def test_militia_upkeep():
    assert B.militia_upkeep_grain(5) == 0
    assert B.militia_upkeep_grain(10) == 0
    assert B.militia_upkeep_grain(14) == 2
    assert B.militia_upkeep_grain(30) == 10


def test_land_upkeep():
    assert B.land_upkeep(1) == 4
    assert B.land_upkeep(2) == 6
    assert B.land_upkeep(9) == 20


def test_claim_costs():
    assert B.claim_cost(2) == 30
    assert B.claim_cost(2, is_wilds=True) == 60


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
    assert prod.grain == B.FARM_YIELD[1] * B.NATIVE_BONUS


def test_tick_militia_disband():
    # Без производства: земля и дружина не оплачиваются из pending
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    state = FiefTickState(
        grain=0,
        goods=0,
        might=30,
        pending_grain=0,
        pending_goods=0,
        pending_might=0,
        actions=1,
        hungry=False,
        tiles=tiles,
        barn_level=0,
    )
    out = apply_fief_tick(state)
    assert out.hungry is True
    assert out.might <= B.MILITIA_FREE
    assert out.militia_disbanded >= 20


def test_tick_pays_land_and_grants_action():
    tiles = [TileView(0, 0, B.TILE_FIELD, 1, B.BLD_FARM, 1)]
    state = FiefTickState(
        grain=50,
        goods=10,
        might=5,
        pending_grain=0,
        pending_goods=0,
        pending_might=0,
        actions=1,
        hungry=False,
        tiles=tiles,
        barn_level=0,
    )
    out = apply_fief_tick(state)
    assert out.hungry is False
    assert out.actions == 2
    assert out.grain == 50 - out.land_upkeep - out.militia_upkeep


def test_raid_fail_loses_all_might():
    r = resolve_raid(
        attacker_name="A",
        victim_name="B",
        attack_might=5,
        watch_defense=40,
        patrol_active=True,
        intercept=True,
        victim_grain=100,
        victim_goods=100,
        barn_level=0,
        victim_daily_grain=10,
        victim_daily_goods=10,
    )
    assert r.success is False
    assert r.might_lost == 5


def test_raid_loot_caps():
    g, d = loot_amounts(0.9, 100, 100, 5, 5)
    assert g + d <= 50  # 25% of 200
    assert g + d <= 20  # 2 days prod


def test_collect_respects_cap():
    g, d, m, *_rest = collect_pending(140, 140, 0, 50, 50, 10, 0)
    assert g == B.DEFAULT_STASH_CAP
    assert d == B.DEFAULT_STASH_CAP
    assert m == 10


def test_best_rectangle_min():
    w, h = B.best_rectangle(12)
    assert w * h >= 12
    assert w * h <= B.MAP_MAX_TILES
