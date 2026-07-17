"""Issue 9: DB transaction helper + safe accept_trade on closed lots.
Critical #1: CAS spend for actions/resources + transaction wrappers.
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.database import Database
from app.engine import Engine


def _db_with_mock_conn() -> tuple[Database, MagicMock]:
    db = Database(connect=False)
    conn = MagicMock()
    db.connection = conn
    db.cursor = MagicMock()
    return db, conn


def _sql(cursor_execute_call) -> str:
    return " ".join(cursor_execute_call[0][0].split())


def _allow_play(db) -> None:
    """Мир в фазе play, тик догнан - ActionWindow открыт."""
    world = {"id": 1, "tick_index": 0, "tick_phase": "play"}
    db.get_world = MagicMock(return_value=world)
    db.get_or_create_world = MagicMock(return_value=world)
    db.list_realms_by_chain = MagicMock(return_value=[])


def test_transaction_commits_on_success():
    db, conn = _db_with_mock_conn()
    with db.transaction():
        db.commit()
        db.commit()
    assert conn.commit.call_count == 1
    conn.rollback.assert_not_called()


def test_transaction_rolls_back_on_error():
    db, conn = _db_with_mock_conn()

    class Boom(Exception):
        pass

    try:
        with db.transaction():
            db.commit()
            raise Boom("fail")
    except Boom:
        pass

    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()
    assert db._tx_depth == 0


def test_nested_transaction_commits_once():
    db, conn = _db_with_mock_conn()
    with db.transaction():
        with db.transaction():
            db.commit()
        db.commit()
    assert conn.commit.call_count == 1


def test_claim_open_trade_returns_none_when_closed():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    db.cursor = cursor

    assert db.claim_open_trade(7) is None
    cursor.execute.assert_called_once()
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "status='open'" in sql
    assert cursor.execute.call_args[0][1] == (7,)


def test_accept_trade_closed_is_safe_noop():
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_trade.return_value = {
        "id": 5,
        "status": "done",
        "expires_at": expires,
        "offerer_fief_id": 2,
        "target_fief_id": None,
        "realm_id": 9,
        "give_res": B.RES_GOODS,
        "give_amt": 5,
        "want_res": B.RES_GRAIN,
        "want_amt": 10,
    }

    engine = Engine(db)
    msg = engine.accept_trade(1, 5)

    assert "закрыт" in msg.lower() or "недоступен" in msg.lower()
    db.update_fief.assert_not_called()
    db.claim_open_trade.assert_not_called()


def test_accept_trade_claim_miss_inside_tx_is_noop():
    """Двойной accept: claim_open_trade проиграл гонку - без переводов."""
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    state = {
        1: {
            "id": 1,
            "realm_id": 9,
            "grain": 50,
            "goods": 20,
            "onboard_step": 4,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
        2: {
            "id": 2,
            "realm_id": 9,
            "grain": 30,
            "goods": 10,
            "onboard_step": 4,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
    }
    trade = {
        "id": 5,
        "status": "open",
        "expires_at": expires,
        "expires_tick": 10,
        "offerer_fief_id": 2,
        "target_fief_id": None,
        "realm_id": 9,
        "give_res": B.RES_GOODS,
        "give_amt": 5,
        "want_res": B.RES_GRAIN,
        "want_amt": 10,
    }
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_trade.return_value = trade
    db.claim_open_trade.return_value = None
    db.get_fief.side_effect = lambda fid: dict(state[fid])
    db.get_realm.return_value = {
        "id": 9,
        "active_minor_key": None,
        "active_minor_until": None,
        "tick_index": 0,
    }

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]

    msg = engine.accept_trade(1, 5)

    assert "закрыт" in msg.lower() or "недоступен" in msg.lower()
    db.update_fief.assert_not_called()
    assert state[1]["grain"] == 50


def test_spend_fief_action_sql_is_cas():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (1, 2)
    cursor.description = [("id",), ("actions",)]

    at = datetime.now(timezone.utc)
    row = db.spend_fief_action(7, last_active_at=at, last_active_tick=3)

    assert row == {"id": 1, "actions": 2}
    sql = _sql(cursor.execute.call_args)
    assert "actions = actions - 1" in sql
    assert "actions >= 1" in sql
    assert "frozen = false" in sql
    assert "returning *" in sql.lower()
    assert cursor.execute.call_args[0][1] == (at, 3, 7)


def test_spend_fief_action_returns_none_when_no_row():
    db, _conn = _db_with_mock_conn()
    db.cursor.fetchone.return_value = None
    at = datetime.now(timezone.utc)
    assert db.spend_fief_action(7, last_active_at=at, last_active_tick=0) is None


def test_debit_fief_resources_sql_is_cas():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (9, 10, 20, 3)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]

    row = db.debit_fief_resources(9, goods=5, might=2)
    assert row["id"] == 9
    sql = _sql(cursor.execute.call_args)
    assert "goods = goods - %s" in sql
    assert "might = might - %s" in sql
    assert "goods >= %s" in sql
    assert "might >= %s" in sql
    assert "returning *" in sql.lower()
    assert cursor.execute.call_args[0][1] == (5, 2, 9, 5, 2)


def test_debit_fief_resources_returns_none_when_insufficient():
    db, _conn = _db_with_mock_conn()
    db.cursor.fetchone.return_value = None
    assert db.debit_fief_resources(1, grain=10) is None


def test_debit_fief_resources_accepts_resource_bag_mapping():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (3, 40, 15, 8)
    cursor.description = [("id",), ("grain",), ("goods",), ("might",)]

    row = db.debit_fief_resources(3, {B.RES_GRAIN: 7, B.RES_GOODS: 2})
    assert row["id"] == 3
    sql = _sql(cursor.execute.call_args)
    assert "grain = grain - %s" in sql
    assert "goods = goods - %s" in sql
    assert cursor.execute.call_args[0][1] == (7, 2, 3, 7, 2)


def test_bump_event_action_uses_on_conflict():
    db, _conn = _db_with_mock_conn()
    db.bump_event_action(3, 8, "might", 5)
    sql = _sql(db.cursor.execute.call_args)
    assert "on conflict" in sql.lower()
    assert "amount = event_actions.amount + excluded.amount" in sql.lower()


def test_spend_action_uses_cas_not_absolute_update():
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    _allow_play(db)
    db.get_realm.return_value = {"tick_index": 4}
    db.spend_fief_action.return_value = {
        "id": 1,
        "actions": 0,
        "frozen": False,
        "user_id": 10,
        "realm_id": 2,
    }
    db.get_user.return_value = {"last_realm_id": 2}
    db.list_fiefs_by_user.return_value = [{"id": 1, "realm_id": 2}]

    engine = Engine(db)
    fief = {
        "id": 1,
        "actions": 1,
        "frozen": False,
        "user_id": 10,
        "realm_id": 2,
    }
    out = engine._spend_action(fief)
    assert out["actions"] == 0
    db.spend_fief_action.assert_called_once()
    db.update_fief.assert_not_called()


def test_spend_action_cas_miss_raises_no_actions():
    db = MagicMock()
    _allow_play(db)
    db.get_realm.return_value = {"tick_index": 0}
    db.spend_fief_action.return_value = None
    db.get_fief.return_value = {
        "id": 1,
        "actions": 0,
        "frozen": False,
        "user_id": 10,
        "realm_id": 2,
    }
    db.get_user.return_value = {"last_realm_id": 2}
    db.list_fiefs_by_user.return_value = [{"id": 1, "realm_id": 2}]

    engine = Engine(db)
    fief = {
        "id": 1,
        "actions": 1,
        "frozen": False,
        "user_id": 10,
        "realm_id": 2,
    }
    with pytest.raises(ValueError, match="Нет действий"):
        engine._spend_action(fief)


def _spender_fief(**extra):
    base = {
        "id": 1,
        "realm_id": 9,
        "user_id": 100,
        "actions": 2,
        "frozen": False,
        "goods": 100,
        "grain": 50,
        "might": 10,
        "name": "A",
        "onboard_step": 4,
    }
    base.update(extra)
    return base


def test_build_or_upgrade_wraps_spend_in_transaction():
    fief = _spender_fief()
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "tile_type": B.TILE_FIELD,
        "building": None,
        "building_level": 0,
        "damaged": False,
    }
    db = MagicMock()
    entered = {"n": 0}

    class Tx:
        def __enter__(self):
            entered["n"] += 1
            return None

        def __exit__(self, *args):
            return False

    db.transaction.side_effect = lambda: Tx()
    _allow_play(db)
    db.get_fief.return_value = dict(fief)
    db.get_tile.return_value = tile
    db.get_realm.return_value = {"id": 9, "active_minor_key": None, "tick_index": 1}
    db.debit_fief_resources.return_value = dict(fief, goods=80)
    db.spend_fief_action.return_value = dict(fief, actions=1)
    db.get_user.return_value = {"last_realm_id": 9}
    db.list_fiefs_by_user.return_value = [dict(fief)]

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    msg = engine.build_or_upgrade(1, 0, 0, B.BLD_FARM)
    assert entered["n"] == 1
    db.spend_fief_action.assert_called_once()
    db.debit_fief_resources.assert_called_once_with(
        1, goods=B.building_upgrade_cost(B.BLD_FARM, 1)
    )
    assert B.BUILDING_NAMES_RU[B.BLD_FARM] in msg


def test_gather_resource_wraps_spend_in_transaction():
    fief = _spender_fief()
    db = MagicMock()
    entered = {"n": 0}

    class Tx:
        def __enter__(self):
            entered["n"] += 1
            return None

        def __exit__(self, *args):
            return False

    db.transaction.side_effect = lambda: Tx()
    _allow_play(db)
    db.get_fief.return_value = dict(fief)
    db.spend_fief_action.return_value = dict(fief, actions=1)
    db.get_realm.return_value = {"tick_index": 2}
    db.get_user.return_value = {"last_realm_id": 9}
    db.list_fiefs_by_user.return_value = [dict(fief)]

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    msg = engine.gather_resource(1, B.RES_MIGHT)
    assert entered["n"] == 1
    db.spend_fief_action.assert_called_once()
    assert f"+{B.GATHER_MIGHT}" in msg


def test_patrol_wraps_spend_in_transaction():
    fief = _spender_fief(might=5)
    db = MagicMock()
    entered = {"n": 0}

    class Tx:
        def __enter__(self):
            entered["n"] += 1
            return None

        def __exit__(self, *args):
            return False

    db.transaction.side_effect = lambda: Tx()
    _allow_play(db)
    db.get_fief.return_value = dict(fief)
    db.spend_fief_action.return_value = dict(fief, actions=1)
    db.get_realm.return_value = {"tick_index": 7}
    db.get_user.return_value = {"last_realm_id": 9}
    db.list_fiefs_by_user.return_value = [dict(fief)]

    engine = Engine(db)
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    msg = engine.patrol(1)
    assert entered["n"] == 1
    db.spend_fief_action.assert_called_once()
    db.update_fief.assert_called()
    kwargs = db.update_fief.call_args.kwargs
    assert kwargs["patrol_until_tick"] == 7 + B.PATROL_TICKS
    assert "Дозор" in msg


def test_demolish_building_wraps_spend_in_transaction():
    fief = _spender_fief()
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "building": B.BLD_FARM,
        "building_level": 1,
        "is_core": False,
    }
    db = MagicMock()
    entered = {"n": 0}

    class Tx:
        def __enter__(self):
            entered["n"] += 1
            return None

        def __exit__(self, *args):
            return False

    db.transaction.side_effect = lambda: Tx()
    _allow_play(db)
    db.get_fief.return_value = dict(fief)
    db.get_tile.return_value = tile
    db.spend_fief_action.return_value = dict(fief, actions=1)
    db.get_realm.return_value = {"tick_index": 1}
    db.get_user.return_value = {"last_realm_id": 9}
    db.list_fiefs_by_user.return_value = [dict(fief)]

    engine = Engine(db)
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    msg = engine.demolish_building(1, 0, 0)
    assert entered["n"] == 1
    db.spend_fief_action.assert_called_once()
    assert "Снесено" in msg


def test_contribute_catastrophe_might_is_atomic():
    fief = _spender_fief(might=8)
    db = MagicMock()
    entered = {"n": 0}

    class Tx:
        def __enter__(self):
            entered["n"] += 1
            return None

        def __exit__(self, *args):
            return False

    db.transaction.side_effect = lambda: Tx()
    _allow_play(db)
    db.get_event.return_value = {
        "id": 3,
        "status": "active",
        "realm_id": 9,
    }
    db.get_fief_by_user.return_value = dict(fief)
    db.get_realm.return_value = {"id": 9, "world_id": 1, "tick_index": 0}
    db.debit_fief_resources.return_value = dict(fief, might=3)
    db.event_actions.return_value = [
        {"amount": 5},
        {"amount": 5},
    ]

    engine = Engine(db)
    total = engine.contribute_catastrophe_might(3, 100, amount=5)
    assert total == 10
    assert entered["n"] == 1
    db.debit_fief_resources.assert_called_once_with(1, might=5)
    db.bump_event_action.assert_called_once_with(3, 1, "might", 5)
    db.update_fief.assert_not_called()


def test_contribute_catastrophe_insufficient_might_no_bump():
    fief = _spender_fief(might=2)
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    _allow_play(db)
    db.get_event.return_value = {"id": 3, "status": "active", "realm_id": 9}
    db.get_fief_by_user.return_value = dict(fief)
    db.get_realm.return_value = {"id": 9, "world_id": 1, "tick_index": 0}
    db.debit_fief_resources.return_value = None

    engine = Engine(db)
    with pytest.raises(ValueError, match="Недостаточно силы"):
        engine.contribute_catastrophe_might(3, 100, amount=5)
    db.bump_event_action.assert_not_called()


def test_double_spend_action_second_cas_miss():
    """Два параллельных списания: второй UPDATE не находит строку."""
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.description = [("id",), ("actions",)]
    cursor.fetchone.side_effect = [(1, 0), None]
    at = datetime.now(timezone.utc)

    first = db.spend_fief_action(1, last_active_at=at, last_active_tick=0)
    second = db.spend_fief_action(1, last_active_at=at, last_active_tick=0)
    assert first is not None
    assert second is None
    assert cursor.execute.call_count == 2


def test_engine_double_spend_action_second_cas_miss_no_side_effects():
    """Второй CAS miss по действию: ошибка, без списания товаров и без клетки.

    Snapshot катастроф/entities - до write-tx (моки без commit через _fetchall).
    Проваленная write-tx по-прежнему не коммитит.
    """
    fief = _spender_fief(goods=100, actions=1)
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "tile_type": B.TILE_FIELD,
        "building": None,
        "building_level": 0,
        "damaged": False,
    }
    db, conn = _db_with_mock_conn()
    db.get_fief = MagicMock(return_value=dict(fief))
    db.get_tile = MagicMock(return_value=tile)
    db.get_realm = MagicMock(
        return_value={"id": 9, "active_minor_key": None, "tick_index": 1}
    )
    db.get_active_events = MagicMock(return_value=[])
    db.list_active_tile_entities = MagicMock(return_value=[])
    db.spend_fief_action = MagicMock(return_value=None)
    db.debit_fief_resources = MagicMock()
    db.update_tile = MagicMock()
    db.get_user = MagicMock(return_value={"last_realm_id": 9})
    db.list_fiefs_by_user = MagicMock(return_value=[dict(fief)])
    _allow_play(db)

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Нет действий"):
        engine.build_or_upgrade(1, 0, 0, B.BLD_FARM)
    db.get_active_events.assert_called()
    db.list_active_tile_entities.assert_called()
    db.spend_fief_action.assert_called_once()
    db.debit_fief_resources.assert_not_called()
    db.update_tile.assert_not_called()
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_build_aborts_before_tile_when_goods_debit_fails():
    """Spend прошёл, debit CAS miss: rollback транзакции, клетка не меняется.

    Catastrophe/entity snapshot читается до write-tx; сам failed write не коммитит.
    """
    fief = _spender_fief(goods=100)
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "tile_type": B.TILE_FIELD,
        "building": None,
        "building_level": 0,
        "damaged": False,
    }
    db, conn = _db_with_mock_conn()
    db.get_fief = MagicMock(return_value=dict(fief))
    db.get_tile = MagicMock(return_value=tile)
    db.get_realm = MagicMock(
        return_value={"id": 9, "active_minor_key": None, "tick_index": 1}
    )
    db.get_active_events = MagicMock(return_value=[])
    db.list_active_tile_entities = MagicMock(return_value=[])
    db.spend_fief_action = MagicMock(return_value=dict(fief, actions=1))
    db.debit_fief_resources = MagicMock(return_value=None)
    db.update_tile = MagicMock()
    db.get_user = MagicMock(return_value={"last_realm_id": 9})
    db.list_fiefs_by_user = MagicMock(return_value=[dict(fief)])
    _allow_play(db)

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Нужно"):
        engine.build_or_upgrade(1, 0, 0, B.BLD_FARM)
    db.get_active_events.assert_called()
    db.list_active_tile_entities.assert_called()
    db.update_tile.assert_not_called()
    db.spend_fief_action.assert_called_once()
    db.debit_fief_resources.assert_called_once()
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_build_reads_catastrophe_snapshot_before_write_transaction():
    """CRITICAL 1: unified collector; get_active_events/entities до входа в write-tx."""
    fief = _spender_fief(goods=100)
    tile = {
        "id": 50,
        "owner_fief_id": 1,
        "is_overgrown": False,
        "tile_type": B.TILE_FIELD,
        "building": None,
        "building_level": 0,
        "damaged": False,
    }
    db, conn = _db_with_mock_conn()
    db.get_fief = MagicMock(return_value=dict(fief))
    db.get_tile = MagicMock(return_value=tile)
    db.get_realm = MagicMock(
        return_value={"id": 9, "active_minor_key": None, "tick_index": 1}
    )
    order: list[str] = []

    def track_events(*_a, **_k):
        order.append("get_active_events")
        return []

    def track_entities(*_a, **_k):
        order.append("list_active_tile_entities")
        return []

    def track_tx():
        order.append("transaction_enter")
        return nullcontext()

    db.get_active_events = MagicMock(side_effect=track_events)
    db.list_active_tile_entities = MagicMock(side_effect=track_entities)
    db.transaction = track_tx  # type: ignore[method-assign]
    db.spend_fief_action = MagicMock(return_value=dict(fief, actions=1))
    db.debit_fief_resources = MagicMock(return_value=dict(fief, goods=90))
    db.update_tile = MagicMock()
    db.get_user = MagicMock(return_value={"last_realm_id": 9})
    db.list_fiefs_by_user = MagicMock(return_value=[dict(fief)])
    _allow_play(db)

    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_is_active_play = MagicMock(return_value=True)  # type: ignore[method-assign]
    engine._onboard_build = MagicMock()  # type: ignore[method-assign]

    engine.build_or_upgrade(1, 0, 0, B.BLD_FARM)
    assert order[0] in ("get_active_events", "list_active_tile_entities")
    assert "transaction_enter" in order
    assert order.index("get_active_events") < order.index("transaction_enter")
    assert order.index("list_active_tile_entities") < order.index("transaction_enter")
    conn.commit.assert_not_called()


def test_raid_interceptor_debit_cas_miss_reresolves_without_interceptor():
    """CAS miss силы перехватчика: пересчёт без него, набег атакующего идёт дальше."""
    atk = {
        "id": 1,
        "realm_id": 9,
        "user_id": 100,
        "name": "A",
        "grain": 40,
        "goods": 10,
        "might": 7,  # уже после escrow 5
        "hungry": False,
        "actions": 0,
        "frozen": False,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": None,
    }
    vic = {
        "id": 2,
        "realm_id": 9,
        "user_id": 200,
        "name": "B",
        "grain": 40,
        "goods": 10,
        "might": 3,
        "hungry": False,
        "actions": 1,
        "frozen": False,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": 50,
    }
    ally = {
        "id": 3,
        "realm_id": 9,
        "user_id": 300,
        "name": "C",
        "grain": 40,
        "goods": 10,
        "might": B.INTERCEPT_MIGHT + 2,
        "hungry": False,
        "cover_allies": True,
        "pact_id": 50,
    }
    fiefs = {1: dict(atk), 2: dict(vic), 3: dict(ally)}
    realm = {
        "id": 9,
        "title": "Долина",
        "tick_index": 4,
        "active_minor_key": None,
        "pending_raid_lines": [],
    }
    intent = {
        "id": 11,
        "world_id": 1,
        "tick_index": 4,
        "fief_id": 1,
        "kind": "raid",
        "status": "locked",
        "payload": {
            "victim_id": 2,
            "might": 5,
            "open_truce": False,
            "via_portal": False,
            "attacker_realm_id": 9,
            "victim_realm_id": 9,
            "escrowed": True,
            "attacker_pact_id": None,
        },
    }

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    def debit_fief_resources(fid, amounts=None, **kwargs):
        if int(fid) == 3:
            return None
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    def credit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            row[col] = int(row.get(col) or 0) + int(amt)
        return dict(row)

    db = MagicMock()
    db.transaction.return_value = nullcontext()
    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.credit_fief_resources.side_effect = credit_fief_resources
    db.get_realm.return_value = realm
    db.realms_are_adjacent.return_value = True
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = [fiefs[2], fiefs[3]]
    db.world_tick_incomplete = MagicMock(return_value=False)
    db.update_realm = MagicMock()
    db.log_raid = MagicMock()
    db.list_raid_intents.return_value = [intent]
    db.claim_resolve_action_intent.return_value = dict(intent, status="resolved")
    db.update_action_intent_payload = MagicMock()
    _allow_play(db)

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(
            defense=0,
            resources=MagicMock(
                return_value={B.RES_GRAIN: 1.0, B.RES_GOODS: 1.0, B.RES_MIGHT: 0.0}
            ),
        )
    )
    engine.barn_level = MagicMock(return_value=0)
    engine._siege_probe_would_succeed = MagicMock(return_value=True)

    ally_might_before = fiefs[3]["might"]
    with patch(
        "app.engine.resolve_raid",
        side_effect=[
            MagicMock(
                public_line="Отбит у ворот",
                success=False,
                might_lost=5,
                stolen={B.RES_GRAIN: 0, B.RES_GOODS: 0},
                intercept_applied=True,
            ),
            MagicMock(
                public_line="A ограбил B",
                success=True,
                might_lost=4,
                stolen={B.RES_GRAIN: 2, B.RES_GOODS: 1},
                intercept_applied=False,
            ),
        ],
    ) as resolve:
        report = engine.resolve_pending_raids(1, 4)

    assert resolve.call_count == 2
    assert resolve.call_args_list[0].kwargs["intercept"] is True
    assert resolve.call_args_list[1].kwargs["intercept"] is False
    assert report.resolved_count == 1
    assert fiefs[3]["might"] == ally_might_before
    assert fiefs[1]["might"] == 7 + 1  # returned commit-lost = 5-4
    assert fiefs[2]["grain"] == 38
    assert fiefs[2]["goods"] == 9
    db.debit_fief_resources.assert_any_call(3, might=B.INTERCEPT_MIGHT)

