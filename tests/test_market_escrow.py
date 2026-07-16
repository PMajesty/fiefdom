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


def test_refund_trade_only_cancels_without_crediting():
    """Без эскроу истечение лота не возвращает ресурс (его и не снимали)."""
    trade = {
        "id": 7,
        "status": "open",
        "offerer_fief_id": 2,
        "give_res": B.RES_GRAIN,
        "give_amt": 4,
    }
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.claim_cancel_open_trade.return_value = {**trade, "status": "cancelled"}

    Engine(db)._refund_trade(trade)

    db.claim_cancel_open_trade.assert_called_once_with(7)
    db.update_fief.assert_not_called()
    db.get_fief.assert_not_called()


def test_accept_trade_takes_give_from_seller_not_escrow():
    state = {
        1: {
            "id": 1,
            "realm_id": 9,
            "grain": 50,
            "goods": 0,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
        2: {
            "id": 2,
            "realm_id": 9,
            "grain": 12,
            "goods": 0,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
    }
    trade = {
        "id": 5,
        "status": "open",
        "expires_tick": 10,
        "offerer_fief_id": 2,
        "target_fief_id": None,
        "realm_id": 9,
        "give_res": B.RES_GRAIN,
        "give_amt": 10,
        "want_res": B.RES_GOODS,
        "want_amt": 5,
    }
    # покупатель платит товарами
    state[1]["goods"] = 20
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_trade.return_value = trade
    db.claim_open_trade.return_value = {**trade, "status": "done"}
    db.realms_are_adjacent.return_value = True
    db.get_fief.side_effect = lambda fid: dict(state[int(fid)])
    db.get_realm.return_value = {
        "id": 9,
        "active_minor_key": None,
        "tick_index": 0,
    }

    def _update(fid, **fields):
        state[int(fid)].update(fields)

    db.update_fief.side_effect = _update
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.barn_level = MagicMock(return_value=1)  # type: ignore[method-assign]
    engine._require_cross_valley_caught_up = MagicMock()  # type: ignore[method-assign]

    msg = engine.accept_trade(1, 5)
    assert msg.startswith("Сделка")
    assert state[2]["grain"] == 2  # 12 - 10 отдано
    assert state[2]["goods"] == 5  # получил оплату
    assert state[1]["goods"] == 15  # 20 - 5
    assert state[1]["grain"] == 60  # 50 + 10


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


def test_annul_patch_runs_v1_and_v2_when_fresh():
    """Несколько лотов одной усадьбы - один UPDATE fiefs с суммами; два патча."""
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    # create table; select v1 miss; refund; insert v1; select v2 miss; refund; insert v2
    cursor.fetchone.side_effect = [None, None]
    db.cursor = cursor

    db._apply_patch_annul_open_trades()

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert any("applied_patches" in sql and "CREATE TABLE" in sql for sql in executed)
    refund_sqls = [sql for sql in executed if "FOR UPDATE" in sql]
    assert len(refund_sqls) == 2
    for refund_sql in refund_sqls:
        assert "GROUP BY offerer_fief_id" in refund_sql
        assert "grain = f.grain + t.grain_amt" in refund_sql
    inserts = [c[0][1][0] for c in cursor.execute.call_args_list if c[0][0].strip().startswith("INSERT INTO applied_patches")]
    assert inserts == [
        "annul_open_trades_no_cancel_v1",
        "annul_open_trades_no_escrow_v2",
    ]


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


def test_annul_patch_skips_when_both_already_applied():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(1,), (1,)]
    db.cursor = cursor

    db._apply_patch_annul_open_trades()

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert not any("FOR UPDATE" in sql for sql in executed)
    assert not any("INSERT INTO applied_patches" in sql for sql in executed)


def test_annul_patch_v2_runs_after_v1():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = [(1,), None]
    db.cursor = cursor

    db._apply_patch_annul_open_trades()

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert sum(1 for sql in executed if "FOR UPDATE" in sql) == 1
    inserts = [
        c[0][1][0]
        for c in cursor.execute.call_args_list
        if c[0][0].strip().startswith("INSERT INTO applied_patches")
    ]
    assert inserts == ["annul_open_trades_no_escrow_v2"]
