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
    assert prod.grain == B.FARM_YIELD[1] * B.NATIVE_BONUS
    assert prod.goods == B.FIEF_BASE_GOODS


def test_fief_base_goods_income():
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    prod = fief_daily_production(tiles)
    assert prod.goods == B.FIEF_BASE_GOODS
    hungry = fief_daily_production(tiles, hungry=True)
    assert hungry.goods == B.FIEF_BASE_GOODS * B.HUNGER_PRODUCTION_MULT
    overgrown = [TileView(0, 0, B.TILE_HILLS, 1, None, 0, is_overgrown=True)]
    assert fief_daily_production(overgrown).goods == 0


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
    assert g + d <= int(0.40 * 200)  # max stash frac
    assert g + d <= int(3 * (5 + 5))  # max days of prod


def test_raid_success_might_loss_severe():
    r = resolve_raid(
        attacker_name="A",
        victim_name="B",
        attack_might=8,
        watch_defense=0,
        patrol_active=False,
        intercept=False,
        victim_grain=200,
        victim_goods=200,
        barn_level=0,
        victim_daily_grain=20,
        victim_daily_goods=20,
    )
    assert r.success is True
    assert r.might_lost == max(1, int(round(8 * B.RAID_SUCCESS_MIGHT_LOSS_FRAC)))
    assert r.grain_stolen + r.goods_stolen > 0
    assert r.public_line == "A ограбил B"
    assert "зерна" not in r.public_line
    assert "товаров" not in r.public_line


def test_collect_respects_cap():
    g, d, m, pg, pd, pm, _notes = collect_pending(140, 140, 0, 50, 50, 10, 0)
    assert g == B.DEFAULT_STASH_CAP
    assert d == B.DEFAULT_STASH_CAP
    assert m == 10
    assert pg == 0.0 and pd == 0.0 and pm == 0.0


def test_collect_pending_can_skip_might():
    g, d, m, pg, pd, pm, _notes = collect_pending(
        10, 10, 7, 20, 15, 8, 0, include_might=False
    )
    assert g == 30
    assert d == 25
    assert m == 7
    assert pg == 0.0 and pd == 0.0
    assert pm == 8


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
