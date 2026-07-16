"""Синхронизация континента: тик, катастрофы, набеги, вторая усадьба."""
from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine import Engine
from app.scheduler import _maybe_post_world_catastrophe


def _world(**overrides):
    data = {
        "id": 1,
        "name": "Континент",
        "day_number": 1,
        "tick_index": 0,
        "timezone": "Europe/Moscow",
        "last_tick_local_date": None,
        "last_tick_slot": None,
        "forced_tick_count": 0,
        "active_minor_key": None,
        "pending_minor_key": "",
        "active_minor_until": None,
        "next_catastrophe_tick": 5,
        "next_catastrophe_key": "bandit_night",
        "last_catastrophe_key": None,
    }
    data.update(overrides)
    return data


def _realm(rid: int, **overrides):
    data = {
        "id": rid,
        "world_id": 1,
        "chain_index": rid - 1,
        "title": f"Долина {rid}",
        "chat_id": -1000 - rid,
        "day_number": 1,
        "timezone": "Europe/Moscow",
        "tick_index": 0,
        "pending_raid_lines": [],
        "active_minor_key": None,
        "pending_minor_key": "",
        "last_economy_tick": None,
        "forced_tick_count": 0,
    }
    data.update(overrides)
    return data


def test_same_realm_raid_appends_pending_line_once():
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    atk = {
        "id": 1,
        "realm_id": 10,
        "user_id": 100,
        "name": "A",
        "grain": 40,
        "goods": 10,
        "might": 10,
        "hungry": False,
        "actions": 1,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": None,
    }
    vic = dict(atk, id=2, user_id=200, name="B", might=3)
    realm = _realm(10, tick_index=3, pending_raid_lines=[])

    db.get_fief.side_effect = lambda fid: atk if int(fid) == 1 else vic
    db.get_realm.return_value = realm
    db.realms_are_adjacent.return_value = True
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = []

    updates: list[list] = []

    def update_realm(rid, **fields):
        if "pending_raid_lines" in fields:
            updates.append(list(fields["pending_raid_lines"]))
            realm["pending_raid_lines"] = fields["pending_raid_lines"]

    db.update_realm.side_effect = update_realm

    engine = Engine(db)
    engine.require_active_fief = MagicMock(return_value=atk)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    with patch(
        "app.engine.resolve_raid",
        return_value=MagicMock(
            public_line="Набег!",
            success=False,
            might_lost=1,
            grain_stolen=0,
            goods_stolen=0,
            intercept_applied=False,
        ),
    ):
        engine.raid(1, 2, might=5)

    assert len(updates) == 1
    assert updates[0] == ["Набег!"]


def test_raids_since_tick_includes_victim_realm():
    from app.database import Database

    sql_holder: dict[str, str] = {}

    class FakeDb(Database):
        def __init__(self):
            self.lock = MagicMock()
            self.connection = MagicMock()
            self.cursor = MagicMock()
            self._tx_depth = 0

        def _fetchall(self, sql, params=None):
            sql_holder["sql"] = sql
            sql_holder["params"] = params
            return []

    FakeDb().raids_since_tick(7, 2)
    assert "victim_realm_id" in sql_holder["sql"]
    assert sql_holder["params"] == (2, 7, 7)


def test_world_tick_equal_clock_and_resume_after_partial_failure():
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    r1 = _realm(1, last_economy_tick=0)
    r2 = _realm(2, last_economy_tick=0)
    world = _world(tick_index=0, pending_minor_key=None)
    chain = [r1, r2]

    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain
    db.clear_world_force_tick_votes = MagicMock(return_value=0)

    def sync(_wid):
        for r in chain:
            r["tick_index"] = world["tick_index"]
            r["day_number"] = world["day_number"]
            r["active_minor_key"] = world.get("active_minor_key")
            r["pending_minor_key"] = world.get("pending_minor_key")

    def update_world(_wid, **fields):
        world.update(fields)
        sync(_wid)

    def update_realm(rid, **fields):
        for r in chain:
            if int(r["id"]) == int(rid):
                r.update(fields)

    db.sync_realms_clock_from_world.side_effect = sync
    db.update_world.side_effect = update_world
    db.update_realm.side_effect = update_realm

    engine = Engine(db)
    calls: list[int] = []

    def fake_realm_tick(rid, tick_slot=None, forced=False, advance_clock=True):
        calls.append(int(rid))
        if int(rid) == 2 and len([c for c in calls if c == 2]) == 1:
            raise RuntimeError("boom")
        return {
            "realm_id": int(rid),
            "digest": f"d{rid}",
            "chat_id": -rid,
            "deserter_event": None,
        }

    engine.run_realm_tick = MagicMock(side_effect=fake_realm_tick)

    with patch("app.engine.roll_minor_event", return_value="fog"):
        first = engine.run_world_tick(1, tick_slot=0)

    assert world["tick_index"] == 1
    assert r1["tick_index"] == 1
    assert r2["tick_index"] == 1
    assert r1["last_economy_tick"] == 1
    assert r2.get("last_economy_tick") in (None, 0)
    assert first["incomplete"] is True
    assert world.get("pending_minor_key") is None

    with patch("app.engine.roll_minor_event", return_value="harvest"):
        second = engine.run_world_tick(1)

    assert second["resumed"] is True
    assert world["tick_index"] == 1
    assert r1["last_economy_tick"] == 1
    assert r2["last_economy_tick"] == 1
    assert second["incomplete"] is False
    assert world["pending_minor_key"] == "harvest"
    assert calls.count(1) == 1
    assert calls.count(2) == 2


@pytest.mark.asyncio
async def test_catastrophe_advances_schedule_before_send_and_resumes_missing():
    engine = MagicMock()
    world = _world(tick_index=10, next_catastrophe_tick=10, next_catastrophe_key="bandit_night")
    r1 = _realm(1, chat_id=-1)
    r2 = _realm(2, chat_id=-2)
    engine.db.list_realms_by_chain.return_value = [r1, r2]
    engine.db.get_active_events.return_value = []
    engine.db.list_fiefs.return_value = [{"id": 1}]
    created = []

    def create_event(**fields):
        ev = {"id": len(created) + 1, **fields}
        created.append(ev)
        return ev

    engine.db.create_event.side_effect = create_event
    engine.db.update_world = MagicMock()
    engine.db.sync_realms_clock_from_world = MagicMock()

    bot = MagicMock()
    send_calls = []

    async def fake_post(*args, **kwargs):
        send_calls.append(args)
        if len(send_calls) == 1:
            raise RuntimeError("telegram down")

    with (
        patch("app.scheduler.send_game", new=fake_post),
        patch("app.scheduler.pick_catastrophe", return_value="cattle_plague"),
        patch("app.scheduler.next_catastrophe_delay_ticks", return_value=7),
    ):
        await _maybe_post_world_catastrophe(bot, engine, world)

    assert len(created) == 2
    assert engine.db.update_world.called
    assert engine.db.sync_realms_clock_from_world.called

    # Resume: одна долина уже с активной волной, второй нет.
    active = {
        "event_key": "bandit_night",
        "resolves_tick": 12,
        "id": 1,
    }
    engine.db.get_active_events.side_effect = lambda rid, kind=None: (
        [active] if int(rid) == 1 else []
    )
    world_due = _world(
        tick_index=10,
        next_catastrophe_tick=10,
        next_catastrophe_key="bandit_night",
    )
    created.clear()
    engine.db.update_world.reset_mock()

    with patch("app.scheduler.send_game", new=AsyncMock()):
        await _maybe_post_world_catastrophe(bot, engine, world_due)

    assert len(created) == 1
    assert created[0]["realm_id"] == 2
    assert created[0]["event_key"] == "bandit_night"
    assert engine.db.update_world.called


def test_join_fief_requires_allow_extra_when_has_other_valley():
    db = MagicMock()
    user = MagicMock(id=42, username="u", full_name="U", first_name="U")
    db.get_fief_by_user.return_value = None
    db.list_fiefs_by_user.return_value = [{"id": 9, "realm_id": 1, "user_id": 42}]
    db._fetchone.return_value = {
        "id": 50,
        "realm_id": 2,
        "owner_fief_id": None,
        "tile_type": "field",
        "x": 1,
        "y": 1,
    }
    db.get_realm.return_value = {"id": 2, "width": 5, "height": 5}
    db.get_tiles.return_value = []

    engine = Engine(db)
    engine.ensure_user = MagicMock()

    with pytest.raises(ValueError, match="осознанно"):
        engine.join_fief(2, user, tile_id=50)

    db.create_fief.return_value = {"id": 11, "realm_id": 2, "name": "U"}
    fief, _msg = engine.join_fief(2, user, tile_id=50, allow_extra_fief=True)
    assert fief["id"] == 11
