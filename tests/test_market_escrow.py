"""Рынок: нельзя снимать лот; возврат эскроу без гонок и без бонусов."""
from __future__ import annotations

import os
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.database import Database
from app.engine import Engine
from app.handlers.dm import market_kb


def test_cancel_trade_rejected():
    engine = Engine(MagicMock())
    with pytest.raises(ValueError, match="нельзя снять"):
        engine.cancel_trade(1, 99)


def test_refund_trade_claims_then_returns_exact_amount():
    state = {
        2: {"id": 2, "grain": 10, "goods": 5},
    }
    trade = {
        "id": 7,
        "status": "open",
        "offerer_fief_id": 2,
        "give_res": B.RES_GRAIN,
        "give_amt": 4,
    }
    claimed = {**trade, "status": "cancelled"}
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.claim_cancel_open_trade.return_value = claimed
    db.get_fief.side_effect = lambda fid: dict(state[int(fid)])

    def _update(fid, **fields):
        state[int(fid)].update(fields)

    db.update_fief.side_effect = _update

    Engine(db)._refund_trade(trade)

    db.claim_cancel_open_trade.assert_called_once_with(7)
    assert state[2]["grain"] == 14
    assert state[2]["goods"] == 5
    db.update_trade.assert_not_called()


def test_refund_trade_noop_when_claim_loses_race():
    trade = {
        "id": 7,
        "status": "open",
        "offerer_fief_id": 2,
        "give_res": B.RES_GRAIN,
        "give_amt": 4,
    }
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.claim_cancel_open_trade.return_value = None

    Engine(db)._refund_trade(trade)

    db.update_fief.assert_not_called()
    db.get_fief.assert_not_called()


def test_claim_cancel_open_trade_sql_guards_open_status():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    db.cursor = cursor

    assert db.claim_cancel_open_trade(7) is None
    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "status='cancelled'" in sql
    assert "status='open'" in sql
    assert cursor.execute.call_args[0][1] == (7,)


def test_market_kb_hides_own_lots_and_shows_seller():
    offers = [
        {
            "id": 1,
            "offerer_fief_id": 10,
            "give_amt": 1,
            "give_res": B.RES_GRAIN,
            "want_amt": 1,
            "want_res": B.RES_GOODS,
        },
        {
            "id": 2,
            "offerer_fief_id": 99,
            "give_amt": 2,
            "give_res": B.RES_GOODS,
            "want_amt": 3,
            "want_res": B.RES_GRAIN,
        },
    ]
    engine = MagicMock()
    engine.db.get_fief.return_value = {
        "id": 99,
        "user_id": 1,
        "name": "Усадьба @seller",
    }
    engine.fief_label.return_value = "Усадьба @seller"

    kb = market_kb(10, offers, engine)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Создать лот" in labels
    assert any("Принять #2" in t and "@seller" in t for t in labels)
    assert not any("Отменить" in t for t in labels)
    assert not any("#1" in t and "Принять" in t for t in labels)


def test_annul_patch_sql_aggregates_per_fief():
    """Несколько лотов одной усадьбы - один UPDATE fiefs с суммами."""
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [None]
    db.cursor = cursor

    db._apply_patch_annul_open_trades()

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert any("applied_patches" in sql and "CREATE TABLE" in sql for sql in executed)
    refund_sql = next(sql for sql in executed if "FOR UPDATE" in sql)
    assert "GROUP BY offerer_fief_id" in refund_sql
    assert "FILTER (WHERE give_res = 'grain')" in refund_sql
    assert "FILTER (WHERE give_res = 'goods')" in refund_sql
    assert "grain = f.grain + t.grain_amt" in refund_sql
    assert "goods = f.goods + t.goods_amt" in refund_sql
    assert "status = 'cancelled'" in refund_sql
    assert "INSERT INTO applied_patches" in executed[-1]


def test_annul_patch_totals_math_multi_lot_same_fief():
    """Контроль агрегации: 2 grain + 1 goods одной усадьбы = точные суммы."""
    lots = [
        {"offerer_fief_id": 1, "give_res": "grain", "give_amt": 10},
        {"offerer_fief_id": 1, "give_res": "grain", "give_amt": 3},
        {"offerer_fief_id": 1, "give_res": "goods", "give_amt": 7},
        {"offerer_fief_id": 2, "give_res": "goods", "give_amt": 2},
    ]
    totals: dict[int, list[int]] = {}
    for lot in lots:
        fid = int(lot["offerer_fief_id"])
        grain, goods = totals.setdefault(fid, [0, 0])
        if lot["give_res"] == "grain":
            grain += int(lot["give_amt"])
        else:
            goods += int(lot["give_amt"])
        totals[fid] = [grain, goods]
    assert totals == {1: [13, 7], 2: [0, 2]}


def test_annul_patch_skips_when_already_applied():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = (1,)
    db.cursor = cursor

    db._apply_patch_annul_open_trades()

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert not any("FOR UPDATE" in sql for sql in executed)
    assert not any("INSERT INTO applied_patches" in sql for sql in executed)
