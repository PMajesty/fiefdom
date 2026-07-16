"""Неактивная долина не копит урожай и действия."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

from app.engine import Engine


def test_inactive_fief_skips_tick_economy():
    realm = {
        "id": 1,
        "world_id": 1,
        "chain_index": 0,
        "title": "A",
        "chat_id": -1,
        "day_number": 3,
        "timezone": "Europe/Moscow",
        "tick_index": 5,
        "pending_raid_lines": [],
        "active_minor_key": None,
        "pending_minor_key": "",
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 0,
        "forced_tick_count": 0,
    }
    active = {
        "id": 10,
        "realm_id": 1,
        "user_id": 100,
        "name": "Active",
        "grain": 40,
        "goods": 20,
        "might": 5,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
        "actions": 1,
        "hungry": False,
        "frozen": False,
    }
    # Тот же user, другая долина - неактивна (last_realm=1).
    inactive_realm = dict(realm, id=2, chain_index=1, title="B", chat_id=-2)
    inactive = dict(active, id=11, realm_id=2, name="Idle", pending_grain=0.0, actions=1)

    db = MagicMock()
    db.transaction.return_value = nullcontext()
    db.get_realm.side_effect = lambda rid: realm if int(rid) == 1 else inactive_realm
    db.list_fiefs.side_effect = lambda rid: [active] if int(rid) == 1 else [inactive]
    db.get_fief.side_effect = lambda fid: active if int(fid) == 10 else inactive
    db.get_user.return_value = {"telegram_id": 100, "last_realm_id": 1}
    db.list_fiefs_by_user.return_value = [active, inactive]
    db.list_expired_open_trades.return_value = []
    db.list_open_trades.return_value = []
    db.get_active_events.return_value = []
    db.raids_since_tick.return_value = []
    db.fief_tiles.return_value = [
        {
            "x": 0,
            "y": 0,
            "tile_type": "field",
            "owner_fief_id": 10,
            "building": "farm",
            "building_level": 1,
            "is_core": True,
            "is_overgrown": False,
        }
    ]
    updated = {}

    def update_fief(fid, **fields):
        updated[int(fid)] = fields

    db.update_fief.side_effect = update_fief
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])
    engine._prepare_tick_minor = MagicMock(return_value=(None, None))
    engine._realm_farm_mult = MagicMock(return_value=1.0)
    engine._drought_mitigated_fief_ids = MagicMock(return_value=set())
    engine._active_cattle_plague = MagicMock(return_value=None)
    engine._rumor_snapshots = MagicMock(return_value=[])
    engine._upcoming_event_hints = MagicMock(return_value=[])
    engine._sunday_extra = MagicMock(return_value=None)

    engine.run_realm_tick(2, advance_clock=False)
    assert 11 not in updated

    engine.run_realm_tick(1, advance_clock=False)
    assert 10 in updated
    assert "pending_grain" in updated[10] or "actions" in updated[10]
