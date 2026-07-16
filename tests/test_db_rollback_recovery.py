"""Ошибка SQL не должна травить общее соединение (25P02)."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.database import Database


def test_fetchone_rolls_back_outside_transaction_on_error():
    db = Database(connect=False)
    db.connection = MagicMock()
    db.cursor = MagicMock()
    db.cursor.execute.side_effect = RuntimeError("deadlock")
    db._tx_depth = 0

    try:
        db._fetchone("SELECT 1;")
        assert False, "expected error"
    except RuntimeError:
        pass

    db.connection.rollback.assert_called_once()


def test_fetchone_commits_read_outside_transaction():
    db = Database(connect=False)
    db.connection = MagicMock()
    db.cursor = MagicMock()
    db.cursor.fetchone.return_value = (1,)
    db.cursor.description = (("id",),)
    db._tx_depth = 0

    row = db._fetchone("SELECT id FROM worlds LIMIT 1;")
    assert row == {"id": 1}
    db.connection.commit.assert_called()
    db.connection.rollback.assert_not_called()


def test_fetchone_error_inside_transaction_does_not_rollback_here():
    db = Database(connect=False)
    db.connection = MagicMock()
    db.cursor = MagicMock()
    db.cursor.execute.side_effect = RuntimeError("boom")
    db._tx_depth = 1

    try:
        db._fetchone("SELECT 1;")
        assert False, "expected error"
    except RuntimeError:
        pass

    db.connection.rollback.assert_not_called()
