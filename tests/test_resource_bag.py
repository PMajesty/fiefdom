"""Critical #4: ResourceBag API - live grain/goods/might, columns stay canonical."""
from __future__ import annotations

import pytest

from app import balance as B
from app.database import Database
from app.domain.economy import Production, TileView, fief_daily_production
from app.domain.resources import (
    LIVE_RESOURCES,
    STASH_CAPPED_RESOURCES,
    UNCAPPED_RESOURCES,
    apply_gather_to_stash,
    apply_production_to_pending,
    empty_pending,
    empty_stash,
    fief_balance_columns,
    migrate_row_balances,
    normalize_debit_amounts,
    pending_columns,
    pending_from_row,
    scale_bag,
    stash_columns,
    stash_from_row,
)
from app.domain.tick import (
    FiefTickState,
    apply_fief_tick,
    collect_pending,
    collect_pending_bags,
)
from unittest.mock import MagicMock


def _sample_fief_row(**overrides):
    row = {
        "id": 7,
        "grain": 41,
        "goods": 17,
        "might": 9,
        "pending_grain": 3.5,
        "pending_goods": 1.25,
        "pending_might": 0.75,
        "actions": 2,
        "hungry": False,
    }
    row.update(overrides)
    return row


def test_live_resources_closed_triad():
    assert LIVE_RESOURCES == (B.RES_GRAIN, B.RES_GOODS, B.RES_MIGHT)
    assert set(empty_stash()) == set(LIVE_RESOURCES)
    assert set(empty_pending()) == set(LIVE_RESOURCES)


def test_bag_adapters_and_collect_policy_sourced_from_live_resources():
    """Adapters and collect sets must track LIVE_RESOURCES, not a parallel triad."""
    assert STASH_CAPPED_RESOURCES == frozenset(B.TRADEABLE)
    assert STASH_CAPPED_RESOURCES <= set(LIVE_RESOURCES)
    assert UNCAPPED_RESOURCES == frozenset(LIVE_RESOURCES) - STASH_CAPPED_RESOURCES
    assert STASH_CAPPED_RESOURCES | UNCAPPED_RESOURCES == set(LIVE_RESOURCES)
    assert STASH_CAPPED_RESOURCES.isdisjoint(UNCAPPED_RESOURCES)

    prod = Production(grain=1.0, goods=2.0, might=3.0, defense=4.0)
    assert set(prod.resources()) == set(LIVE_RESOURCES)
    assert Production.from_resources(prod.resources(), defense=4.0) == prod

    state = FiefTickState(
        grain=1,
        goods=2,
        might=3,
        pending_grain=0.5,
        pending_goods=1.5,
        pending_might=2.5,
        actions=0,
        hungry=False,
        tiles=[],
        barn_level=0,
    )
    assert set(state.stash_bag()) == set(LIVE_RESOURCES)
    assert set(state.pending_bag()) == set(LIVE_RESOURCES)
    outcome_cols = apply_fief_tick(state).balance_columns()
    stash_keys = {k for k in outcome_cols if not k.startswith("pending_")}
    pending_keys = {
        k.removeprefix("pending_")
        for k in outcome_cols
        if k.startswith("pending_")
    }
    assert stash_keys == set(LIVE_RESOURCES)
    assert pending_keys == set(LIVE_RESOURCES)


def test_migrate_row_balances_identity_roundtrip():
    row = _sample_fief_row()
    migrated = migrate_row_balances(row)
    assert migrated["grain"] == 41
    assert migrated["goods"] == 17
    assert migrated["might"] == 9
    assert migrated["pending_grain"] == 3.5
    assert migrated["pending_goods"] == 1.25
    assert migrated["pending_might"] == 0.75
    assert migrate_row_balances(migrated) == migrated


def test_migrate_row_balances_idempotent_on_zeros_and_none():
    row = {
        "grain": 0,
        "goods": None,
        "might": 5,
        "pending_grain": 0,
        "pending_goods": None,
        "pending_might": 2.0,
    }
    once = migrate_row_balances(row)
    twice = migrate_row_balances(once)
    assert once == twice
    assert once["goods"] == 0
    assert once["pending_goods"] == 0.0


def test_stash_pending_column_roundtrip_ignores_unknown_keys():
    bag = stash_from_row({"grain": 10, "goods": 20, "might": 3, "gold": 99, "fish": 1})
    assert bag == {B.RES_GRAIN: 10, B.RES_GOODS: 20, B.RES_MIGHT: 3}
    cols = stash_columns({"grain": 10, "goods": 20, "might": 3, "gold": 99})
    assert cols == {"grain": 10, "goods": 20, "might": 3}
    pending = pending_from_row(
        {
            "pending_grain": 1.5,
            "pending_goods": 2.5,
            "pending_might": 0.5,
            "pending_gold": 100,
        }
    )
    assert pending_columns(pending) == {
        "pending_grain": 1.5,
        "pending_goods": 2.5,
        "pending_might": 0.5,
    }


def test_fief_balance_columns_matches_legacy_update_kwargs():
    stash = {B.RES_GRAIN: 8, B.RES_GOODS: 4, B.RES_MIGHT: 2}
    pending = {B.RES_GRAIN: 1.0, B.RES_GOODS: 0.0, B.RES_MIGHT: 0.5}
    assert fief_balance_columns(stash, pending) == {
        "grain": 8,
        "goods": 4,
        "might": 2,
        "pending_grain": 1.0,
        "pending_goods": 0.0,
        "pending_might": 0.5,
    }


def test_production_resources_bag_roundtrip():
    prod = Production(grain=3.0, goods=5.0, might=1.5, defense=9.0)
    assert prod.resources() == {
        B.RES_GRAIN: 3.0,
        B.RES_GOODS: 5.0,
        B.RES_MIGHT: 1.5,
    }
    back = Production.from_resources(prod.resources(), defense=prod.defense)
    assert back == prod
    scaled = prod.scale(0.5)
    assert scaled.grain == 1.5
    assert scaled.goods == 2.5
    assert scaled.might == 0.75
    assert scaled.defense == 9.0
    assert scale_bag(prod.resources(), 0.5) == scaled.resources()


def test_production_plus_matches_manual_sum():
    a = Production(grain=1, goods=2, might=3, defense=4)
    b = Production(grain=10, goods=20, might=30, defense=40)
    assert a.plus(b) == Production(grain=11, goods=22, might=33, defense=44)


def test_fief_daily_production_unchanged_via_bag_path():
    tiles = [
        TileView(0, 0, B.TILE_FIELD, 1, B.BLD_FARM, 1),
        TileView(1, 0, B.TILE_HILLS, 1, None, 0),
    ]
    prod = fief_daily_production(tiles)
    assert prod.grain == B.FARM_YIELD[1] * B.NATIVE_BONUS
    assert prod.goods == B.FIEF_BASE_GOODS
    bag = prod.resources()
    assert bag[B.RES_GRAIN] == prod.grain
    assert bag[B.RES_GOODS] == prod.goods
    assert bag[B.RES_MIGHT] == prod.might


def test_apply_production_to_pending_caps_like_tick():
    pending = empty_pending()
    production = {B.RES_GRAIN: 10.0, B.RES_GOODS: 4.0, B.RES_MIGHT: 2.0}
    day1 = apply_production_to_pending(pending, production, cap_days=3)
    assert day1[B.RES_GRAIN] == 10.0
    day3 = day1
    for _ in range(2):
        day3 = apply_production_to_pending(day3, production, cap_days=3)
    assert day3[B.RES_GRAIN] == 30.0
    day4 = apply_production_to_pending(day3, production, cap_days=3)
    assert day4[B.RES_GRAIN] == 30.0


def test_collect_pending_bags_matches_legacy_tuple_api():
    legacy = collect_pending(140, 140, 0, 50, 50, 10, 0)
    bags = collect_pending_bags(
        {B.RES_GRAIN: 140, B.RES_GOODS: 140, B.RES_MIGHT: 0},
        {B.RES_GRAIN: 50.0, B.RES_GOODS: 50.0, B.RES_MIGHT: 10.0},
        0,
    )
    stash, pending, notes = bags
    assert (
        stash[B.RES_GRAIN],
        stash[B.RES_GOODS],
        stash[B.RES_MIGHT],
        pending[B.RES_GRAIN],
        pending[B.RES_GOODS],
        pending[B.RES_MIGHT],
        notes,
    ) == legacy


def test_collect_pending_skip_might_via_bags():
    stash, pending, _notes = collect_pending_bags(
        {B.RES_GRAIN: 0, B.RES_GOODS: 0, B.RES_MIGHT: 1},
        {B.RES_GRAIN: 5.0, B.RES_GOODS: 5.0, B.RES_MIGHT: 3.0},
        0,
        include_might=False,
    )
    assert stash[B.RES_MIGHT] == 1
    assert pending[B.RES_MIGHT] == 3.0
    assert pending[B.RES_GRAIN] == 0.0


def test_apply_gather_to_stash_respects_cap():
    stash = {B.RES_GRAIN: 140, B.RES_GOODS: 10, B.RES_MIGHT: 5}
    after, gained = apply_gather_to_stash(
        stash, B.RES_GRAIN, B.GATHER_GRAIN, cap=B.DEFAULT_STASH_CAP
    )
    assert gained == B.DEFAULT_STASH_CAP - 140
    assert after[B.RES_GRAIN] == B.DEFAULT_STASH_CAP
    assert after[B.RES_GOODS] == 10
    assert after[B.RES_MIGHT] == 5


def test_apply_gather_might_ignores_stash_cap():
    stash = empty_stash()
    after, gained = apply_gather_to_stash(stash, B.RES_MIGHT, B.GATHER_MIGHT, cap=0)
    assert gained == B.GATHER_MIGHT
    assert after[B.RES_MIGHT] == B.GATHER_MIGHT


def test_tick_from_fief_row_and_balance_columns_roundtrip_idle_fields():
    row = _sample_fief_row(actions=1, hungry=False)
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    state = FiefTickState.from_fief_row(row, tiles, barn_level=0)
    assert state.grain == 41
    assert state.goods == 17
    assert state.might == 9
    assert state.pending_grain == 3.5
    # Idle deploy helper: migrate without running tick must not alter balances.
    assert migrate_row_balances(row) == fief_balance_columns(
        state.stash_bag(), state.pending_bag()
    )


def test_tick_numeric_outcome_unchanged_for_fixed_balances():
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
    cols = out.balance_columns()
    assert cols["grain"] == out.grain
    assert cols["goods"] == out.goods
    assert cols["might"] == out.might
    assert cols["pending_grain"] == out.pending_grain
    assert cols["pending_goods"] == out.pending_goods
    assert cols["pending_might"] == out.pending_might


def test_normalize_debit_amounts_kwargs_and_mapping():
    assert normalize_debit_amounts(goods=5, might=2) == {"goods": 5, "might": 2}
    assert normalize_debit_amounts({B.RES_GRAIN: 3}) == {"grain": 3}
    assert normalize_debit_amounts({B.RES_GOODS: 1}, might=4) == {
        "goods": 1,
        "might": 4,
    }
    with pytest.raises(ValueError, match="пустой"):
        normalize_debit_amounts()
    with pytest.raises(ValueError, match="колонка"):
        normalize_debit_amounts(gold=1)
    with pytest.raises(ValueError, match="> 0"):
        normalize_debit_amounts(grain=0)


def _db_with_mock_conn() -> tuple[Database, MagicMock]:
    db = Database(connect=False)
    conn = MagicMock()
    db.connection = conn
    db.cursor = MagicMock()
    return db, conn


def test_debit_fief_resources_accepts_mapping():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (9, 10, 20, 3)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]

    row = db.debit_fief_resources(9, {B.RES_GOODS: 5, B.RES_MIGHT: 2})
    assert row["id"] == 9
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "goods = goods - %s" in sql
    assert "might = might - %s" in sql
    assert cursor.execute.call_args[0][1] == (5, 2, 9, 5, 2)


def test_debit_fief_resources_mapping_and_kwargs_same_sql_as_kwargs_only():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (1, 100, 50, 10)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]

    db.debit_fief_resources(1, grain=10)
    kwargs_params = cursor.execute.call_args[0][1]

    cursor.reset_mock()
    cursor.fetchone.return_value = (1, 100, 50, 10)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]
    db.debit_fief_resources(1, {B.RES_GRAIN: 10})
    mapping_params = cursor.execute.call_args[0][1]
    assert mapping_params == kwargs_params


def test_tradeable_still_only_grain_and_goods():
    assert B.TRADEABLE == (B.RES_GRAIN, B.RES_GOODS)
    assert B.RES_MIGHT not in B.TRADEABLE


def test_starting_amounts_roundtrip_through_bags():
    row = {
        "grain": B.STARTING_GRAIN,
        "goods": B.STARTING_GOODS,
        "might": B.STARTING_MIGHT,
        "pending_grain": 0,
        "pending_goods": 0,
        "pending_might": 0,
    }
    assert migrate_row_balances(row) == {
        "grain": B.STARTING_GRAIN,
        "goods": B.STARTING_GOODS,
        "might": B.STARTING_MIGHT,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
    }
