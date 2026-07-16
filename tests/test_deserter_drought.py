"""Клейм дезертира и полив засухи (Issue 6)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app.domain.events import minor_effect
from app.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def test_claim_deserter_first_wins_second_fails():
    db = MagicMock()
    ev = {
        "id": 11,
        "realm_id": 1,
        "event_key": "deserter",
        "status": "active",
    }
    db.get_event.return_value = ev
    db.get_fief_by_user.side_effect = [
        {"id": 101, "realm_id": 1, "might": 5, "frozen": False},
        {"id": 102, "realm_id": 1, "might": 8, "frozen": False},
    ]
    db.try_claim_deserter.side_effect = [True, False]

    engine = Engine(db)
    assert engine.claim_deserter(11, user_id=1001) == "ok"
    assert engine.claim_deserter(11, user_id=1002) == "already_taken"

    bonus = int(minor_effect("deserter")["first_claim_might"])
    db.try_claim_deserter.assert_any_call(11, 101, bonus)
    assert db.try_claim_deserter.call_count == 2


def test_claim_deserter_resolved_event_already_taken():
    db = MagicMock()
    db.get_event.return_value = {
        "id": 11,
        "realm_id": 1,
        "event_key": "deserter",
        "status": "resolved",
    }
    engine = Engine(db)
    assert engine.claim_deserter(11, user_id=1) == "already_taken"
    db.try_claim_deserter.assert_not_called()


def test_mitigate_drought_spends_goods_and_marks_fief():
    until = _utcnow() + timedelta(hours=12)
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
        "active_minor_until": until,
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
    until = _utcnow() + timedelta(hours=12)
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
        "active_minor_until": until,
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
    until = _utcnow() + timedelta(hours=12)
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
        "active_minor_until": until,
    }
    engine = Engine(db)
    try:
        engine.mitigate_drought(7)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "товар" in str(exc).lower()


def test_fief_can_mitigate_drought():
    until = _utcnow() + timedelta(hours=6)
    db = MagicMock()
    db.get_fief.return_value = {"id": 2, "realm_id": 1, "frozen": False}
    db.get_realm.return_value = {
        "id": 1,
        "active_minor_key": "drought",
        "active_minor_until": until,
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


def test_shipped_includes_deserter_and_drought():
    from app.domain import events

    assert "deserter" in events.SHIPPED_MINOR_KEYS
    assert "drought" in events.SHIPPED_MINOR_KEYS
    assert "trader" not in events.SHIPPED_MINOR_KEYS
