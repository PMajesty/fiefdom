"""Чистая логика deploy/claim_cost_refund.py (без живой БД)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app import balance as B

_SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "claim_cost_refund.py"


def _load_ops():
    name = "claim_cost_refund_ops"
    spec = importlib.util.spec_from_file_location(name, _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ops = _load_ops()


def test_active_tile_count_skips_overgrown():
    tiles = [
        {"is_overgrown": False},
        {"is_overgrown": True},
        {"is_overgrown": False},
        {},
    ]
    assert ops.active_tile_count(tiles) == 3


def test_plan_from_fief_tile_counts_matches_delta_table():
    planned = ops.plan_from_fief_tile_counts(
        [
            (1, 10, 1),
            (2, 10, 4),
            (3, 11, 9),
            (4, 11, 2),
        ]
    )
    assert planned == [
        (2, 10, 4, B.claim_cost_refund_delta(4)),
        (3, 11, 9, B.claim_cost_refund_delta(9)),
    ]
    assert planned[0][3] == 45
    assert planned[1][3] == 1345


def test_apply_refunds_replace_marker_deletes_before_insert():
    db = MagicMock()
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx
    db.credit_fief_resources.return_value = {"id": 1, "goods": 99}

    ops.apply_refunds(db, [(1, 2, 4, 45)], replace_marker=True)

    deletes = [
        c
        for c in db.cursor.execute.call_args_list
        if c.args and "DELETE FROM applied_patches" in str(c.args[0])
    ]
    inserts = [
        c
        for c in db.cursor.execute.call_args_list
        if c.args and "INSERT INTO applied_patches" in str(c.args[0])
    ]
    assert len(deletes) == 1
    assert len(inserts) == 1
    db.credit_fief_resources.assert_called_once_with(1, goods=45)


def test_apply_refunds_without_replace_does_not_delete():
    db = MagicMock()
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx
    db.credit_fief_resources.return_value = {"id": 1}

    ops.apply_refunds(db, [(1, 2, 4, 45)], replace_marker=False)

    delete_sql = [
        c.args[0]
        for c in db.cursor.execute.call_args_list
        if c.args and "DELETE FROM applied_patches" in str(c.args[0])
    ]
    assert delete_sql == []


def test_apply_refunds_rolls_back_concept_on_credit_fail():
    db = MagicMock()
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx
    db.credit_fief_resources.return_value = None

    with pytest.raises(RuntimeError, match="credit failed"):
        ops.apply_refunds(db, [(9, 1, 5, 120)])
