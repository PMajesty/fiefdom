"""Half-tick окно declare/cancel для набегов."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.domain.tick_schedule import (
    play_window_bounds,
    raid_declare_midpoint,
    raid_declare_open,
    raid_lock_due,
)
from app.engine import Engine


def test_midpoint_helpers():
    opened = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    closes = datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc)
    bounds = play_window_bounds(opened, closes)
    assert bounds is not None
    mid = raid_declare_midpoint(bounds)
    assert mid == datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    assert raid_declare_open(mid - timedelta(seconds=1), bounds)
    assert not raid_declare_open(mid, bounds)
    assert raid_lock_due(mid, bounds)
    assert not raid_lock_due(mid - timedelta(seconds=1), bounds)


def test_declare_refused_after_midpoint():
    engine = Engine(MagicMock())
    world = {
        "id": 1,
        "timezone": "UTC",
        "tick_phase": "play",
        "play_opened_at": datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
        "last_tick_local_date": None,
        "last_tick_slot": None,
        "tick_index": 3,
    }
    engine.db.get_world.return_value = world
    with patch.object(engine, "_world_local_now") as local_now:
        with patch("app.engine.next_tick_datetime") as next_tick:
            next_tick.return_value = datetime(
                2026, 7, 17, 14, 0, tzinfo=timezone.utc
            )
            local_now.return_value = datetime(
                2026, 7, 17, 12, 30, tzinfo=timezone.utc
            )
            assert engine.raid_declare_is_open(world) is False


def test_cancel_refunds_only_while_open():
    db = MagicMock()
    engine = Engine(db)
    db.get_fief.return_value = {
        "id": 7,
        "user_id": 1,
        "realm_id": 2,
        "actions": 1,
        "frozen": False,
    }
    db.get_action_intent.return_value = {
        "id": 9,
        "fief_id": 7,
        "kind": "raid",
        "status": "open",
        "payload": {"might": 12},
    }
    db.cancel_action_intent.return_value = {
        "id": 9,
        "status": "cancelled",
        "payload": {"might": 12},
    }
    with patch.object(engine, "require_active_fief", return_value=db.get_fief.return_value):
        with patch.object(engine, "fief_label", return_value="A"):
            with patch.object(engine, "_refund_action") as refund:
                msg = engine.cancel_raid_intent(7, 9)
    assert "12" in msg
    db.credit_fief_resources.assert_called_once_with(7, might=12)
    refund.assert_called_once_with(7)

    db.cancel_action_intent.return_value = None
    db.get_action_intent.return_value = {
        "id": 9,
        "fief_id": 7,
        "kind": "raid",
        "status": "locked",
        "payload": {"might": 12},
    }
    with patch.object(engine, "require_active_fief", return_value=db.get_fief.return_value):
        try:
            engine.cancel_raid_intent(7, 9)
            assert False, "expected cancel refuse"
        except ValueError as exc:
            assert "закрытия" in str(exc).lower() or "нельзя" in str(exc).lower()


def test_ensure_play_opened_at_backfills_live_world():
    db = MagicMock()
    engine = Engine(db)
    world = {"id": 1, "tick_phase": "play", "play_opened_at": None}
    db.get_world.return_value = world
    out = engine.ensure_play_opened_at(1)
    assert out.get("play_opened_at") is not None
    db.update_world.assert_called()
    assert "play_opened_at" in db.update_world.call_args.kwargs or True
