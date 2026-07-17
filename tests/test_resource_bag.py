"""ResourceBag API: registry-driven grain/goods/might, columns stay canonical."""
from __future__ import annotations

from random import Random
from unittest.mock import MagicMock

import pytest

from app import balance as B
from app.database import Database
from app.domain.economy import Production, TileView, fief_daily_production
from app.domain.raids import loot_amounts, resolve_raid
from app.domain.resources import (
    RESOURCE_DEFS,
    ResourceDef,
    apply_gather_to_stash,
    apply_production_to_pending,
    empty_pending,
    empty_stash,
    fief_balance_columns,
    gather_result_text,
    live_resource_keys,
    migrate_row_balances,
    normalize_debit_amounts,
    pending_columns,
    pending_from_row,
    resource_name_ru,
    scale_bag,
    stash_capped_keys,
    stash_columns,
    stash_from_row,
    tradeable_keys,
    tradeable_synonym_alternatives,
    uncapped_keys,
)
from app.domain.tick import FiefTickState, apply_fief_tick, collect_pending_bags
from app.resource_schema import (
    build_annul_open_trades_sql,
    build_credit_sql,
    build_debit_sql,
    ensure_resource_columns_sql,
    fief_stash_ddl_lines,
    raid_stolen_column_map,
    raid_stolen_fields,
)


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


def _tick_state(**kw) -> FiefTickState:
    return FiefTickState(
        stash={
            B.RES_GRAIN: kw.get("grain", 0),
            B.RES_GOODS: kw.get("goods", 0),
            B.RES_MIGHT: kw.get("might", 0),
        },
        pending={
            B.RES_GRAIN: float(kw.get("pending_grain", 0)),
            B.RES_GOODS: float(kw.get("pending_goods", 0)),
            B.RES_MIGHT: float(kw.get("pending_might", 0)),
        },
        actions=kw["actions"],
        hungry=kw.get("hungry", False),
        tiles=kw["tiles"],
        barn_level=kw.get("barn_level", 0),
    )


def test_live_resources_closed_triad():
    keys = live_resource_keys()
    assert keys == (B.RES_GRAIN, B.RES_GOODS, B.RES_MIGHT)
    assert tuple(r.key for r in RESOURCE_DEFS) == keys
    assert set(empty_stash()) == set(keys)
    assert set(empty_pending()) == set(keys)
    assert tradeable_keys() == (B.RES_GRAIN, B.RES_GOODS)
    assert resource_name_ru(B.RES_GRAIN) == "Зерно"


def test_bag_adapters_and_collect_policy_sourced_from_live_resources():
    keys = live_resource_keys()
    capped = stash_capped_keys()
    uncapped = uncapped_keys()
    assert capped == frozenset(tradeable_keys())
    assert capped <= set(keys)
    assert uncapped == frozenset(keys) - capped
    assert capped | uncapped == set(keys)
    assert capped.isdisjoint(uncapped)

    prod = Production(grain=1.0, goods=2.0, might=3.0, defense=4.0)
    assert set(prod.resources()) == set(keys)
    assert Production.from_resources(prod.resources(), defense=4.0) == prod

    state = _tick_state(
        grain=1,
        goods=2,
        might=3,
        pending_grain=0.5,
        pending_goods=1.5,
        pending_might=2.5,
        actions=0,
        tiles=[],
        barn_level=0,
    )
    assert set(state.stash_bag()) == set(keys)
    assert set(state.pending_bag()) == set(keys)
    outcome_cols = apply_fief_tick(state).balance_columns()
    stash_keys = {k for k in outcome_cols if not k.startswith("pending_")}
    pending_keys = {
        k.removeprefix("pending_")
        for k in outcome_cols
        if k.startswith("pending_")
    }
    assert stash_keys == set(keys)
    assert pending_keys == set(keys)


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
    assert scaled.resources()[B.RES_GRAIN] == 1.5
    assert scaled.resources()[B.RES_GOODS] == 2.5
    assert scaled.resources()[B.RES_MIGHT] == 0.75
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
    bag = prod.resources()
    assert bag[B.RES_GRAIN] == B.FARM_YIELD[1] * B.NATIVE_BONUS
    assert bag[B.RES_GOODS] == B.FIEF_BASE_GOODS
    assert bag[B.RES_MIGHT] == 0.0


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


def test_collect_pending_bags_cap_and_might():
    stash, pending, notes = collect_pending_bags(
        {B.RES_GRAIN: 140, B.RES_GOODS: 140, B.RES_MIGHT: 0},
        {B.RES_GRAIN: 50.0, B.RES_GOODS: 50.0, B.RES_MIGHT: 10.0},
        0,
    )
    assert stash[B.RES_GRAIN] == B.DEFAULT_STASH_CAP
    assert stash[B.RES_GOODS] == B.DEFAULT_STASH_CAP
    assert stash[B.RES_MIGHT] == 10
    assert pending[B.RES_GRAIN] == 0.0
    assert notes


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
    assert state.stash[B.RES_GRAIN] == 41
    assert state.stash[B.RES_GOODS] == 17
    assert state.stash[B.RES_MIGHT] == 9
    assert state.pending[B.RES_GRAIN] == 3.5
    assert migrate_row_balances(row) == fief_balance_columns(
        state.stash_bag(), state.pending_bag()
    )


def test_tick_numeric_outcome_unchanged_for_fixed_balances():
    tiles = [TileView(0, 0, B.TILE_FIELD, 1, B.BLD_FARM, 1)]
    state = _tick_state(
        grain=50, goods=10, might=5, actions=1, tiles=tiles, barn_level=0
    )
    out = apply_fief_tick(state)
    assert out.hungry is False
    assert out.actions == 2
    assert out.stash[B.RES_GRAIN] == 50 - out.land_upkeep - out.militia_upkeep
    cols = out.balance_columns()
    assert cols["grain"] == out.stash[B.RES_GRAIN]
    assert cols["goods"] == out.stash[B.RES_GOODS]
    assert cols["might"] == out.stash[B.RES_MIGHT]
    assert cols["pending_grain"] == out.pending[B.RES_GRAIN]
    assert cols["pending_goods"] == out.pending[B.RES_GOODS]
    assert cols["pending_might"] == out.pending[B.RES_MIGHT]


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
    assert tradeable_keys() == (B.RES_GRAIN, B.RES_GOODS)
    assert B.RES_MIGHT not in tradeable_keys()


def test_gather_amounts_dict_is_source():
    assert B.GATHER_AMOUNTS[B.RES_GRAIN] == B.GATHER_GRAIN == 12
    assert B.gather_amount(B.RES_GOODS) == B.GATHER_GOODS
    with pytest.raises(ValueError, match="Нельзя собрать"):
        B.gather_amount("ore")


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


def test_registry_drives_db_column_mapping_and_debit_credit_sql():
    assert raid_stolen_column_map() == {
        B.RES_GRAIN: "grain_stolen",
        B.RES_GOODS: "goods_stolen",
    }
    assert raid_stolen_fields({B.RES_GRAIN: 4, B.RES_GOODS: 2}) == {
        "grain_stolen": 4,
        "goods_stolen": 2,
    }
    ddl = ", ".join(fief_stash_ddl_lines())
    assert "grain INT NOT NULL DEFAULT 0" in ddl
    assert "pending_might DOUBLE PRECISION NOT NULL DEFAULT 0" in ddl
    assert any("pending_grain" in s for s in ensure_resource_columns_sql())
    assert any("grain_stolen" in s for s in ensure_resource_columns_sql())

    annul = build_annul_open_trades_sql()
    assert "give_res = 'grain'" in annul
    assert "give_res = 'goods'" in annul
    assert "grain = f.grain + t.grain_amt" in annul
    assert "goods = f.goods + t.goods_amt" in annul

    norm = normalize_debit_amounts(grain=3, goods=1)
    debit_sql, debit_params = build_debit_sql(norm, 9)
    assert "grain = grain - %s" in debit_sql
    assert "goods = goods - %s" in debit_sql
    assert debit_params == (3, 1, 9, 3, 1)
    credit_sql, credit_params = build_credit_sql(norm, 9)
    assert "grain = grain + %s" in credit_sql
    assert credit_params == (3, 1, 9)

    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (9, 13, 21, 3)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]
    row = db.credit_fief_resources(9, {B.RES_GRAIN: 3, B.RES_GOODS: 1})
    assert row["id"] == 9
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "grain = grain + %s" in sql
    assert cursor.execute.call_args[0][1] == (3, 1, 9)


def test_gather_trade_send_golden_current_resources():
    assert gather_result_text(B.RES_GRAIN, 12, 12) == (
        "Сбор: +12 зерна (−1 действие)."
    )
    assert gather_result_text(B.RES_GRAIN, 5, 12) == (
        "Сбор: +5 зерна (−1 действие). (склад почти полон)"
    )
    assert gather_result_text(B.RES_GOODS, 10, 10) == (
        "Сбор: +10 товаров (−1 действие)."
    )
    assert gather_result_text(B.RES_MIGHT, 2, 2) == (
        "Сбор: +2 силы (−1 действие)."
    )

    from app.handlers.dm import (
        _parse_send_line,
        gather_resources_kb,
    )

    assert set(tradeable_synonym_alternatives().split("|")) == {
        "зерно",
        "товары",
        "grain",
        "goods",
    }
    assert _parse_send_line("зерно 10") == (B.RES_GRAIN, 10)
    assert _parse_send_line("goods 3") == (B.RES_GOODS, 3)
    assert _parse_send_line("сила 5") is None

    kb = gather_resources_kb(7)
    gth_buttons = [
        btn
        for row in kb.inline_keyboard
        for btn in row
        if (btn.callback_data or "").startswith("gth:")
    ]
    assert [btn.text for btn in gth_buttons] == [
        f"Зерно +{B.GATHER_GRAIN}",
        f"Товары +{B.GATHER_GOODS}",
        f"Сила +{B.GATHER_MIGHT}",
    ]
    assert [btn.callback_data for btn in gth_buttons] == [
        f"gth:7:{B.RES_GRAIN}",
        f"gth:7:{B.RES_GOODS}",
        f"gth:7:{B.RES_MIGHT}",
    ]


def test_extra_registry_resource_flows_without_code_monkeypatches(monkeypatch):
    """4-й ресурс: только данные реестра + GATHER_AMOUNTS, lootable и gatherable."""
    from app.domain import resources as res_mod
    from app.handlers.dm import (
        _parse_send_line,
        gather_resources_kb,
    )

    fake = ResourceDef(
        key="ore",
        name_ru="Руда",
        name_ru_genitive="руды",
        name_ru_object="руду",
        synonyms=("руда", "ore"),
        tradeable=True,
        stash_capped=True,
        raid_lootable=True,
        raid_stolen_column="ore_stolen",
        status_emoji="🪨",
    )
    monkeypatch.setattr(res_mod, "RESOURCE_DEFS", (*RESOURCE_DEFS, fake))
    monkeypatch.setitem(B.GATHER_AMOUNTS, "ore", 3)

    assert "ore" in live_resource_keys()
    assert empty_stash()["ore"] == 0
    assert B.gather_amount("ore") == 3

    prod = Production(grain=1.0, goods=2.0, might=3.0, ore=4.5, defense=9.0)
    assert prod.resources()["ore"] == 4.5
    assert prod.scale(2.0).resources()["ore"] == 9.0

    state = FiefTickState(
        stash={
            B.RES_GRAIN: 50,
            B.RES_GOODS: 10,
            B.RES_MIGHT: 5,
            "ore": 8,
        },
        pending={
            B.RES_GRAIN: 0.0,
            B.RES_GOODS: 0.0,
            B.RES_MIGHT: 0.0,
            "ore": 2.5,
        },
        actions=1,
        hungry=False,
        tiles=[TileView(0, 0, B.TILE_HILLS, 1, None, 0)],
        barn_level=0,
    )
    out = apply_fief_tick(state)
    assert out.stash["ore"] == 8
    assert out.pending["ore"] == 2.5

    loot = loot_amounts(
        0.9,
        {B.RES_GRAIN: 0, B.RES_GOODS: 0, "ore": 80},
        {B.RES_GRAIN: 0.0, B.RES_GOODS: 0.0, "ore": 20.0},
        loot_keys=(B.RES_GRAIN, B.RES_GOODS, "ore"),
        rng=Random(0),
    )
    assert set(loot) == {B.RES_GRAIN, B.RES_GOODS, "ore"}
    assert loot["ore"] > 0
    assert loot[B.RES_GRAIN] == 0
    assert loot[B.RES_GOODS] == 0

    raid = resolve_raid(
        attacker_name="A",
        victim_name="B",
        attack_might=40,
        watch_defense=0,
        patrol_active=False,
        intercept=False,
        victim_stash={
            B.RES_GRAIN: 0,
            B.RES_GOODS: 0,
            B.RES_MIGHT: 0,
            "ore": 120,
        },
        barn_level=0,
        victim_daily={
            B.RES_GRAIN: 0.0,
            B.RES_GOODS: 0.0,
            B.RES_MIGHT: 0.0,
            "ore": 30.0,
        },
        rng=Random(1),
    )
    assert raid.success is True
    assert raid.stolen["ore"] > 0
    assert raid.stolen.get(B.RES_GRAIN, 0) == 0
    assert raid.stolen.get(B.RES_GOODS, 0) == 0

    after, gained = apply_gather_to_stash(
        empty_stash(), "ore", B.gather_amount("ore"), cap=B.DEFAULT_STASH_CAP
    )
    assert gained == 3
    assert after["ore"] == 3

    assert _parse_send_line("ore 7") == ("ore", 7)
    kb = gather_resources_kb(3)
    ore_btn = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if (btn.callback_data or "") == "gth:3:ore"
    )
    assert ore_btn.text == "Руда +3"
    assert raid_stolen_column_map()["ore"] == "ore_stolen"
    assert any("ore INT NOT NULL DEFAULT 0" in line for line in fief_stash_ddl_lines())
