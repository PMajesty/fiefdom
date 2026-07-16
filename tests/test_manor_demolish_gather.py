"""Двор, снос и плоский сбор."""
from __future__ import annotations

from app import balance as B
from app.domain.economy import TileView, building_production, fief_daily_production


def test_manor_production_profile():
    p = building_production(B.BLD_MANOR, 1, B.TILE_FIELD)
    assert p.grain == B.MANOR_GRAIN
    assert p.goods == B.MANOR_GOODS
    assert p.might == B.MANOR_MIGHT
    farm = building_production(B.BLD_FARM, 1, B.TILE_FIELD)
    assert p.grain < farm.grain
    workshop = building_production(B.BLD_WORKSHOP, 1, B.TILE_FOREST)
    assert p.goods > workshop.goods


def test_manor_might_respects_free_cap():
    tiles = [
        TileView(
            x=0,
            y=0,
            tile_type=B.TILE_FIELD,
            owner_fief_id=1,
            building=B.BLD_MANOR,
            building_level=1,
            is_core=True,
        )
    ]
    at_cap = fief_daily_production(tiles, current_might=B.MILITIA_FREE)
    assert at_cap.might == 0.0
    below = fief_daily_production(tiles, current_might=B.MILITIA_FREE - 1)
    assert below.might == 1.0
    low = fief_daily_production(tiles, current_might=0)
    assert low.might == float(B.MANOR_MIGHT)


def test_demolish_refund_includes_upgrades():
    invested = B.building_invested_goods(B.BLD_FARM, 3)
    assert invested == 20 + 50 + 120
    refund = B.demolish_refund_goods(B.BLD_FARM, 3)
    assert refund == int(invested * B.DEMOLISH_REFUND_FRAC)


def test_gather_amounts():
    assert B.gather_amount(B.RES_GRAIN) == B.GATHER_GRAIN
    assert B.gather_amount(B.RES_GOODS) == B.GATHER_GOODS
    assert B.gather_amount(B.RES_MIGHT) == B.GATHER_MIGHT


def test_player_buildings_exclude_manor():
    assert B.BLD_MANOR not in B.PLAYER_BUILDINGS
    assert B.BLD_MANOR in B.BUILDING_NAMES_RU
    assert B.build_action_cost(B.BLD_MANOR, {"building": None, "building_level": 0}) is None
    assert (
        B.build_action_cost(
            B.BLD_FARM,
            {"building": B.BLD_MANOR, "building_level": 1, "damaged": False},
        )
        is None
    )
