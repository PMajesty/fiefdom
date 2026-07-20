"""Двор, снос и плоский сбор."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app import balance as B
from app.domain.production import TileView, building_production, fief_daily_production
from app.engine import Engine
from app.ui.keyboards.flow import demolish_tiles_kb


def test_manor_production_profile():
    p = building_production(B.BLD_MANOR, 1, B.TILE_FIELD)
    assert p.resources()[B.RES_GRAIN] == B.MANOR_GRAIN
    assert p.resources()[B.RES_GOODS] == B.MANOR_GOODS
    assert p.resources()[B.RES_MIGHT] == B.MANOR_MIGHT
    farm = building_production(B.BLD_FARM, 1, B.TILE_FIELD)
    assert p.resources()[B.RES_GRAIN] < farm.resources()[B.RES_GRAIN]
    workshop = building_production(B.BLD_WORKSHOP, 1, B.TILE_FOREST)
    assert p.resources()[B.RES_GOODS] > workshop.resources()[B.RES_GOODS]


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
    assert at_cap.resources()[B.RES_MIGHT] == 0.0
    below = fief_daily_production(tiles, current_might=B.MILITIA_FREE - 1)
    assert below.resources()[B.RES_MIGHT] == 1.0
    low = fief_daily_production(tiles, current_might=0)
    assert low.resources()[B.RES_MIGHT] == float(B.MANOR_MIGHT)


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


def test_demolish_tiles_kb_allows_non_manor_on_core():
    tiles = [
        {
            "x": 0,
            "y": 0,
            "building": B.BLD_MANOR,
            "building_level": 1,
            "is_core": True,
        },
        {
            "x": 1,
            "y": 0,
            "building": B.BLD_FARM,
            "building_level": 2,
            "is_core": True,
        },
        {
            "x": 2,
            "y": 0,
            "building": B.BLD_WORKSHOP,
            "building_level": 1,
            "is_core": False,
        },
    ]
    kb = demolish_tiles_kb(7, tiles)
    callbacks = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("dml:7:")
    }
    assert "dml:7:1:0" in callbacks
    assert "dml:7:2:0" in callbacks
    assert "dml:7:0:0" not in callbacks


def test_demolish_building_allows_farm_on_core_tile():
    fief = {
        "id": 1,
        "realm_id": 9,
        "user_id": 100,
        "actions": 2,
        "frozen": False,
        "goods": 40,
        "grain": 10,
        "might": 5,
        "name": "A",
        "onboard_step": 4,
    }
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "building": B.BLD_FARM,
        "building_level": 1,
        "is_core": True,
    }
    db = MagicMock()
    db.transaction.return_value.__enter__ = MagicMock(return_value=None)
    db.transaction.return_value.__exit__ = MagicMock(return_value=False)
    db.get_fief.return_value = dict(fief)
    db.get_tile.return_value = tile
    db.spend_fief_action.return_value = dict(fief, actions=1)
    db.get_realm.return_value = {"tick_index": 1}
    db.get_user.return_value = {"last_realm_id": 9}
    db.list_fiefs_by_user.return_value = [dict(fief)]
    world = {"id": 1, "tick_index": 0, "tick_phase": "play"}
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = []

    engine = Engine(db)
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    msg = engine.demolish_building(1, 1, 0)
    assert "Снесено" in msg
    db.update_tile.assert_called_once_with(
        50, building=None, building_level=0, damaged=False
    )


def test_demolish_building_rejects_manor():
    fief = {
        "id": 1,
        "realm_id": 9,
        "user_id": 100,
        "actions": 2,
        "frozen": False,
        "goods": 40,
        "name": "A",
        "onboard_step": 4,
    }
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "building": B.BLD_MANOR,
        "building_level": 1,
        "is_core": True,
    }
    db = MagicMock()
    db.get_fief.return_value = dict(fief)
    db.get_tile.return_value = tile
    engine = Engine(db)
    with pytest.raises(ValueError, match="Двор снести нельзя"):
        engine.demolish_building(1, 0, 0)
    db.spend_fief_action.assert_not_called()
