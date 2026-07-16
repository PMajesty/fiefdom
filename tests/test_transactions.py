"""Issue 9: DB transaction helper + safe accept_trade on closed lots."""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

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
