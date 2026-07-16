"""Полив засухи."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.domain.events import minor_effect
from app.engine import Engine


def test_mitigate_drought_spends_goods_and_marks_fief():
    db = MagicMock()
    db.get_fief.return_value = {
        "id": 7,
        "realm_id": 3,
        "goods": 25,
        "frozen": False,
    }
    db.get_realm.return_value = {
        "id": 3,
        "active_minor_key": "drought",
        "tick_index": 5,
    }
    db.get_active_events.return_value = [
        {
            "id": 44,
            "event_key": "drought",
            "status": "active",
            "payload": {"mitigated_fief_ids": []},
        }
    ]

    engine = Engine(db)
    assert engine.mitigate_drought(7) == "ok"

    cost = int(minor_effect("drought")["mitigate"]["goods"])
    db.update_fief.assert_called_once_with(7, goods=25 - cost)
    db.update_event.assert_called_once()
    _args, kwargs = db.update_event.call_args
    assert _args[0] == 44
    assert kwargs["payload"]["mitigated_fief_ids"] == [7]


def test_mitigate_drought_second_call_already():
    db = MagicMock()
    db.get_fief.return_value = {
        "id": 7,
        "realm_id": 3,
        "goods": 25,
        "frozen": False,
    }
    db.get_realm.return_value = {
        "id": 3,
        "active_minor_key": "drought",
        "tick_index": 5,
    }
    db.get_active_events.return_value = [
        {
            "id": 44,
            "event_key": "drought",
            "status": "active",
            "payload": {"mitigated_fief_ids": [7]},
        }
    ]

    engine = Engine(db)
    assert engine.mitigate_drought(7) == "already"
    db.update_fief.assert_not_called()


def test_mitigate_drought_insufficient_goods():
    db = MagicMock()
    db.get_fief.return_value = {
        "id": 7,
        "realm_id": 3,
        "goods": 3,
        "frozen": False,
    }
    db.get_realm.return_value = {
        "id": 3,
        "active_minor_key": "drought",
        "tick_index": 5,
    }
    engine = Engine(db)
    try:
        engine.mitigate_drought(7)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "товар" in str(exc).lower()


def test_fief_can_mitigate_drought():
    db = MagicMock()
    db.get_fief.return_value = {"id": 2, "realm_id": 1, "frozen": False}
    db.get_realm.return_value = {
        "id": 1,
        "active_minor_key": "drought",
        "tick_index": 3,
    }
    db.get_active_events.return_value = [
        {"id": 1, "event_key": "drought", "payload": {"mitigated_fief_ids": []}}
    ]
    engine = Engine(db)
    assert engine.fief_can_mitigate_drought(2) is True

    db.get_active_events.return_value = [
        {"id": 1, "event_key": "drought", "payload": {"mitigated_fief_ids": [2]}}
    ]
    assert engine.fief_can_mitigate_drought(2) is False


def test_shipped_includes_drought_not_deserter():
    from app.domain import events

    assert "deserter" not in events.SHIPPED_MINOR_KEYS
    assert "deserter" not in events.MINOR_EVENTS
    assert "drought" in events.SHIPPED_MINOR_KEYS
    assert "trader" not in events.SHIPPED_MINOR_KEYS
