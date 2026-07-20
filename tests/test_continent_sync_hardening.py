"""Синхронизация континента: тик, катастрофы, набеги, вторая усадьба."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app import balance as B
from app.engine import Engine
from app.scheduler import _maybe_post_world_catastrophe
from app.services.catastrophes import CatastropheService


def _wire_catastrophe_facades(engine: MagicMock) -> MagicMock:
    """MagicMock Engine: facades делегируют в реальный CatastropheService."""
    engine.plan_world_catastrophe.side_effect = (
        lambda world: CatastropheService(engine, engine.db).plan_world_catastrophe(
            world
        )
    )
    engine.iter_expired_catastrophe_resolutions.side_effect = (
        lambda realm: CatastropheService(
            engine, engine.db
        ).iter_expired_resolutions(realm)
    )
    return engine


def _freeze_msk(year: int, month: int, day: int, hour: int = 12):
    fixed_now = datetime(year, month, day, hour, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    return _FrozenDateTime


def _ready_clock_world(**world_overrides):
    """Мир с одной долиной, готовый к новому advance (не resume)."""
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    world = _world(**world_overrides)
    tick = int(world.get("tick_index") or 0)
    r1 = _realm(
        1,
        last_economy_tick=tick,
        tick_index=tick,
        day_number=int(world.get("day_number") or 1),
    )
    chain = [r1]
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def sync(_wid):
        for r in chain:
            for k in (
                "day_number",
                "tick_index",
                "last_tick_local_date",
                "last_tick_slot",
                "active_minor_key",
                "pending_minor_key",
                "tick_phase",
            ):
                if k in world:
                    r[k] = world[k]

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
    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -1}
    )
    return engine, world, r1


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
        "tick_phase": "play",
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
        "onboard_step": 4,
    }
    vic = dict(atk, id=2, user_id=200, name="B", might=3)
    realm = _realm(10, tick_index=3, day_number=5, pending_raid_lines=[])

    db.get_fief.side_effect = lambda fid: atk if int(fid) == 1 else vic
    db.get_realm.return_value = realm
    db.realms_are_adjacent.return_value = True
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = []
    world = _world(tick_index=3, tick_phase="play")
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = [
        _realm(10, tick_index=3, last_economy_tick=3)
    ]

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

    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine._require_cross_valley_caught_up = MagicMock()
    engine._format_raid_deadline = MagicMock(return_value="12:00")
    db.get_world.return_value = {
        "id": 1,
        "tick_index": 3,
        "tick_phase": "play",
        "timezone": "UTC",
    }
    db.realms_are_adjacent.return_value = True
    db.list_open_raid_intents_for_fief.return_value = []
    intents = []

    def create_action_intent(**fields):
        row = {
            "id": 1,
            "status": "locked",
            **fields,
            "payload": dict(fields.get("payload") or {}),
        }
        intents.append(row)
        return row

    db.create_action_intent.side_effect = create_action_intent
    db.list_raid_intents.side_effect = lambda *a, **k: [
        dict(i, status="locked") for i in intents
    ]
    db.claim_resolve_action_intent.side_effect = lambda iid: next(
        (dict(i, status="resolved") for i in intents if int(i["id"]) == int(iid)),
        None,
    )
    db.credit_fief_resources = MagicMock()
    db.update_action_intent_payload = MagicMock()
    engine.declare_raid(1, 2, 5)
    with patch(
        "app.services.night_raids.resolve_raid",
        return_value=MagicMock(
            public_line="Набег!",
            success=False,
            might_lost=1,
            stolen={B.RES_GRAIN: 0, B.RES_GOODS: 0},
            intercept_applied=False,
        ),
    ):
        engine.resolve_pending_raids(1, 3)

    assert any("Набег!" in line for batch in updates for line in batch)


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


def test_world_clock_advance_and_realm_sync_are_atomic():
    """Падение между update_world и sync откатывает сдвиг часов мира.

    Без общего transaction() мир остаётся на новом tick_index, а долины -
    на старом: resume гоняет economy против stale realm clock.
    """
    db = MagicMock()
    world = _world(tick_index=0, pending_minor_key="")
    r1 = _realm(1, last_economy_tick=0, tick_index=0, day_number=1)
    chain = [r1]

    @contextmanager
    def atomic_tx():
        snap_w = dict(world)
        snap_r = [dict(r) for r in chain]
        try:
            yield
        except Exception:
            world.clear()
            world.update(snap_w)
            for r, snap in zip(chain, snap_r):
                r.clear()
                r.update(snap)
            raise

    db.transaction.side_effect = lambda: atomic_tx()
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def update_world(_wid, **fields):
        world.update(fields)

    def sync(_wid):
        raise RuntimeError("crash after world advance")

    db.update_world.side_effect = update_world
    db.sync_realms_clock_from_world.side_effect = sync
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -1}
    )

    with (
        patch("app.services.world_tick.roll_minor_event", return_value="fog"),
        pytest.raises(RuntimeError, match="crash after world advance"),
    ):
        engine.run_world_tick(1, tick_slot=0)

    assert world["tick_index"] == 0
    assert world["day_number"] == 1
    assert r1["tick_index"] == 0
    assert r1["day_number"] == 1
    engine.run_realm_tick.assert_not_called()


def test_clock_advance_updates_world_and_sync_inside_same_transaction():
    """update_world и sync в одной transaction() при сдвиге часов."""
    db = MagicMock()
    world = _world(tick_index=0, pending_minor_key="", forced_tick_count=0)
    r1 = _realm(1, last_economy_tick=0)
    chain = [r1]
    depth = {"n": 0}
    ops: list[tuple[str, int]] = []

    @contextmanager
    def tracking_tx():
        depth["n"] += 1
        try:
            yield
        finally:
            depth["n"] -= 1

    db.transaction.side_effect = lambda: tracking_tx()
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def update_world(_wid, **fields):
        ops.append(("update_world", depth["n"]))
        world.update(fields)

    def sync(_wid):
        ops.append(("sync", depth["n"]))
        for r in chain:
            r["tick_index"] = world["tick_index"]
            r["day_number"] = world["day_number"]
            r["forced_tick_count"] = world.get("forced_tick_count")

    def update_realm(rid, **fields):
        for r in chain:
            if int(r["id"]) == int(rid):
                r.update(fields)

    db.update_world.side_effect = update_world
    db.sync_realms_clock_from_world.side_effect = sync
    db.update_realm.side_effect = update_realm

    engine = Engine(db)
    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -1}
    )

    with patch("app.services.world_tick.roll_minor_event", return_value=None):
        engine.run_world_tick(1, tick_slot=0)

    advance = [o for o in ops if o[0] in ("update_world", "sync")]
    assert advance[:2] == [
        ("update_world", 1),
        ("sync", 1),
    ]


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

    def fake_realm_tick(rid, tick_slot=None, *, advance_clock=True):
        calls.append(int(rid))
        if int(rid) == 2 and len([c for c in calls if c == 2]) == 1:
            raise RuntimeError("boom")
        return {
            "realm_id": int(rid),
            "digest": f"d{rid}",
            "chat_id": -rid,
        }

    engine.run_realm_tick = MagicMock(side_effect=fake_realm_tick)

    with patch("app.services.world_tick.roll_minor_event", return_value="fog"):
        first = engine.run_world_tick(1, tick_slot=0)

    assert world["tick_index"] == 1
    assert r1["tick_index"] == 1
    assert r2["tick_index"] == 1
    assert r1["last_economy_tick"] == 1
    assert r2.get("last_economy_tick") in (None, 0)
    assert first["incomplete"] is True
    assert world.get("pending_minor_key") is None
    assert world["tick_phase"] == "economy"

    with patch("app.services.world_tick.roll_minor_event", return_value="harvest"):
        second = engine.run_world_tick(1)

    assert second["resumed"] is True
    assert world["tick_index"] == 1
    assert r1["last_economy_tick"] == 1
    assert r2["last_economy_tick"] == 1
    assert second["incomplete"] is False
    assert world["pending_minor_key"] == "harvest"
    assert world["tick_phase"] == "play"
    assert calls.count(1) == 1
    assert calls.count(2) == 2


def test_calendar_day_bumps_once_across_same_local_date_slots():
    """4 слота одного локального дня: day_number +1 один раз, tick_index +4."""
    engine, world, _r1 = _ready_clock_world(
        tick_index=4,
        day_number=10,
        last_tick_local_date=date(2026, 7, 16),
        last_tick_slot=3,
        pending_minor_key="",
    )
    frozen = _freeze_msk(2026, 7, 17)
    with (
        patch("app.engine.datetime", frozen),
        patch("app.services.world_tick.datetime", frozen),
        patch("app.services.world_tick.roll_minor_event", return_value=None),
    ):
        for slot in (0, 1, 2, 3):
            engine.run_world_tick(1, tick_slot=slot)

    assert world["tick_index"] == 8
    assert world["day_number"] == 11
    assert world["last_tick_local_date"] == date(2026, 7, 17)
    assert world["last_tick_slot"] == 3


def test_calendar_day_bumps_on_new_local_date():
    engine, world, _r1 = _ready_clock_world(
        tick_index=8,
        day_number=11,
        last_tick_local_date=date(2026, 7, 17),
        last_tick_slot=3,
        pending_minor_key="",
    )
    frozen = _freeze_msk(2026, 7, 18, hour=10)
    with (
        patch("app.engine.datetime", frozen),
        patch("app.services.world_tick.datetime", frozen),
        patch("app.services.world_tick.roll_minor_event", return_value=None),
    ):
        engine.run_world_tick(1, tick_slot=0)

    assert world["tick_index"] == 9
    assert world["day_number"] == 12
    assert world["last_tick_local_date"] == date(2026, 7, 18)


def test_calendar_day_unchanged_on_resume_incomplete():
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    r1 = _realm(1, last_economy_tick=0, tick_index=0, day_number=10)
    r2 = _realm(2, last_economy_tick=0, tick_index=0, day_number=10)
    world = _world(
        tick_index=0,
        day_number=10,
        pending_minor_key=None,
        last_tick_local_date=date(2026, 7, 16),
        last_tick_slot=3,
    )
    chain = [r1, r2]
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def sync(_wid):
        for r in chain:
            r["tick_index"] = world["tick_index"]
            r["day_number"] = world["day_number"]
            r["last_tick_local_date"] = world.get("last_tick_local_date")
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

    def fake_realm_tick(rid, tick_slot=None, *, advance_clock=True):
        calls.append(int(rid))
        if int(rid) == 2 and len([c for c in calls if c == 2]) == 1:
            raise RuntimeError("boom")
        return {"realm_id": int(rid), "digest": f"d{rid}", "chat_id": -rid}

    engine.run_realm_tick = MagicMock(side_effect=fake_realm_tick)
    frozen = _freeze_msk(2026, 7, 17)

    with (
        patch("app.engine.datetime", frozen),
        patch("app.services.world_tick.datetime", frozen),
        patch("app.services.world_tick.roll_minor_event", return_value="fog"),
    ):
        first = engine.run_world_tick(1, tick_slot=0)
    assert first["incomplete"] is True
    assert world["tick_index"] == 1
    assert world["day_number"] == 11

    with patch("app.services.world_tick.roll_minor_event", return_value="harvest"):
        second = engine.run_world_tick(1)
    assert second["resumed"] is True
    assert world["tick_index"] == 1
    assert world["day_number"] == 11


def test_admin_tick_without_slot_does_not_bump_day_number():
    engine, world, _r1 = _ready_clock_world(
        tick_index=5,
        day_number=10,
        last_tick_local_date=date(2026, 7, 16),
        last_tick_slot=2,
        pending_minor_key="",
    )
    frozen = _freeze_msk(2026, 7, 17)
    with (
        patch("app.engine.datetime", frozen),
        patch("app.services.world_tick.datetime", frozen),
        patch("app.services.world_tick.roll_minor_event", return_value=None),
    ):
        engine.run_world_tick(1, tick_slot=None)

    assert world["tick_index"] == 6
    assert world["day_number"] == 10
    assert world["last_tick_local_date"] == date(2026, 7, 16)
    assert world["last_tick_slot"] == 2


def test_empty_world_tick_does_not_bump_calendar_day():
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    world = _world(tick_index=3, day_number=7, last_tick_local_date=date(2026, 7, 16))
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = []

    def update_world(_wid, **fields):
        world.update(fields)

    db.update_world.side_effect = update_world
    engine = Engine(db)

    result = engine.run_world_tick(1, tick_slot=0)
    assert result["realms"] == []
    assert world["tick_index"] == 3
    assert world["day_number"] == 7


@pytest.mark.asyncio
async def test_catastrophe_advances_schedule_before_send_and_resumes_missing():
    engine = _wire_catastrophe_facades(MagicMock())
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
        patch("app.scheduler.post_realm_public", new=fake_post),
        patch("app.services.catastrophes.pick_catastrophe", return_value="cattle_plague"),
        patch("app.services.catastrophes.next_catastrophe_delay_ticks", return_value=7),
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

    with patch("app.scheduler.post_realm_public", new=AsyncMock()):
        await _maybe_post_world_catastrophe(bot, engine, world_due)

    assert len(created) == 1
    assert created[0]["realm_id"] == 2
    assert created[0]["event_key"] == "bandit_night"
    assert engine.db.update_world.called


@pytest.mark.asyncio
async def test_catastrophe_announce_text_failure_isolates_realms():
    """Сбой сборки текста для одной долины не отменяет create/announce остальных."""
    engine = _wire_catastrophe_facades(MagicMock())
    world = _world(
        tick_index=10, next_catastrophe_tick=10, next_catastrophe_key="bandit_night"
    )
    r1 = _realm(1, chat_id=-1)
    r2 = _realm(2, chat_id=-2)
    engine.db.list_realms_by_chain.return_value = [r1, r2]
    engine.db.get_active_events.return_value = []

    def list_fiefs(rid):
        if int(rid) == 1:
            raise RuntimeError("db blip")
        return [{"id": 2}]

    engine.db.list_fiefs.side_effect = list_fiefs
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

    async def fake_post(bot_arg, realm_id, text, reply_markup=None):
        send_calls.append(int(realm_id))

    with (
        patch("app.scheduler.post_realm_public", new=fake_post),
        patch(
            "app.services.catastrophes.pick_catastrophe",
            return_value="cattle_plague",
        ),
        patch(
            "app.services.catastrophes.next_catastrophe_delay_ticks",
            return_value=7,
        ),
    ):
        await _maybe_post_world_catastrophe(bot, engine, world)

    assert len(created) == 2
    assert send_calls == [2]


@pytest.mark.asyncio
async def test_catastrophe_heals_divergent_active_keys_to_canonical_wave():
    """Разные активные ключи не должны вечно стопорить fan-out."""
    engine = _wire_catastrophe_facades(MagicMock())
    world = _world(
        tick_index=10,
        next_catastrophe_tick=10,
        next_catastrophe_key="cattle_plague",
    )
    r1 = _realm(1, chat_id=-1)
    r2 = _realm(2, chat_id=-2)
    r3 = _realm(3, chat_id=-3)
    engine.db.list_realms_by_chain.return_value = [r1, r2, r3]
    engine.db.list_fiefs.return_value = [{"id": 1}]

    bandit_a = {"id": 11, "event_key": "bandit_night", "resolves_tick": 12}
    plague = {"id": 22, "event_key": "cattle_plague", "resolves_tick": 14}
    bandit_b = {"id": 33, "event_key": "bandit_night", "resolves_tick": 12}
    # Большинство (2) - bandit; расходящийся plague закрывается, на r2 создаётся канон.
    engine.db.get_active_events.side_effect = lambda rid, kind=None: {
        1: [bandit_a],
        2: [plague],
        3: [bandit_b],
    }.get(int(rid), [])

    created: list[dict] = []
    resolved: list[tuple] = []

    def create_event(**fields):
        ev = {"id": 100 + len(created), **fields}
        created.append(ev)
        return ev

    def update_event(eid, **fields):
        resolved.append((int(eid), fields))

    engine.db.create_event.side_effect = create_event
    engine.db.update_event.side_effect = update_event
    engine.db.update_world = MagicMock()
    engine.db.sync_realms_clock_from_world = MagicMock()

    bot = MagicMock()
    with (
        patch("app.scheduler.post_realm_public", new=AsyncMock()),
        patch("app.services.catastrophes.pick_catastrophe", return_value="cattle_plague"),
        patch("app.services.catastrophes.next_catastrophe_delay_ticks", return_value=7),
    ):
        await _maybe_post_world_catastrophe(bot, engine, world)

    assert resolved == [(22, {"status": "resolved"})]
    assert len(created) == 1
    assert created[0]["realm_id"] == 2
    assert created[0]["event_key"] == "bandit_night"
    assert created[0]["resolves_tick"] == 12
    assert created[0]["status"] == "active"
    # Расписание due - сдвигаем, как при обычном resume; новой волны нет.
    assert engine.db.update_world.called
    assert engine.db.create_event.call_count == 1


@pytest.mark.asyncio
async def test_catastrophe_heal_tie_prefers_last_catastrophe_key_when_schedule_advanced():
    """При равном большинстве и уже сдвинутом расписании канон - last_catastrophe_key."""
    engine = _wire_catastrophe_facades(MagicMock())
    world = _world(
        tick_index=10,
        next_catastrophe_tick=20,
        next_catastrophe_key="bandit_night",
        last_catastrophe_key="cattle_plague",
    )
    r1 = _realm(1, chat_id=-1)
    r2 = _realm(2, chat_id=-2)
    engine.db.list_realms_by_chain.return_value = [r1, r2]
    engine.db.list_fiefs.return_value = [{"id": 1}]

    bandit = {"id": 11, "event_key": "bandit_night", "resolves_tick": 12}
    plague = {"id": 22, "event_key": "cattle_plague", "resolves_tick": 14}
    engine.db.get_active_events.side_effect = lambda rid, kind=None: (
        [bandit] if int(rid) == 1 else [plague]
    )

    created: list[dict] = []
    resolved: list[tuple] = []

    def create_event(**fields):
        ev = {"id": 100 + len(created), **fields}
        created.append(ev)
        return ev

    def update_event(eid, **fields):
        resolved.append((int(eid), fields))

    engine.db.create_event.side_effect = create_event
    engine.db.update_event.side_effect = update_event
    engine.db.update_world = MagicMock()

    bot = MagicMock()
    with patch("app.scheduler.post_realm_public", new=AsyncMock()):
        await _maybe_post_world_catastrophe(bot, engine, world)

    assert resolved == [(11, {"status": "resolved"})]
    assert len(created) == 1
    assert created[0]["realm_id"] == 1
    assert created[0]["event_key"] == "cattle_plague"
    assert created[0]["resolves_tick"] == 14
    assert created[0]["status"] == "active"
    # Расписание уже сдвинуто - не двигаем и не открываем вторую волну.
    assert not engine.db.update_world.called


@pytest.mark.asyncio
async def test_catastrophe_heal_expired_wave_no_bandit_fail_penalties():
    """Просроченный канон: placeholder resolved, без fail-штрафов по зерну."""
    from app.scheduler import _resolve_expired_catastrophes

    engine = _wire_catastrophe_facades(MagicMock())
    world = _world(
        tick_index=12,
        next_catastrophe_tick=20,
        next_catastrophe_key="cattle_plague",
        last_catastrophe_key="bandit_night",
    )
    r1 = _realm(1, chat_id=-1, tick_index=12)
    r2 = _realm(2, chat_id=-2, tick_index=12)
    r3 = _realm(3, chat_id=-3, tick_index=12)
    engine.db.list_realms_by_chain.return_value = [r1, r2, r3]

    # Большинство - просроченный bandit; r2 - дивергент; r3 - без события.
    bandit_a = {"id": 11, "event_key": "bandit_night", "resolves_tick": 10, "status": "active"}
    plague = {"id": 22, "event_key": "cattle_plague", "resolves_tick": 10, "status": "active"}
    active_by_realm = {
        1: [bandit_a],
        2: [plague],
        3: [],
    }

    def get_active_events(rid, kind=None):
        return list(active_by_realm.get(int(rid), []))

    engine.db.get_active_events.side_effect = get_active_events

    created: list[dict] = []
    resolved: list[tuple] = []
    fief_updates: list[tuple] = []

    def create_event(**fields):
        ev = {"id": 100 + len(created), **fields}
        created.append(ev)
        if fields.get("status") == "active":
            active_by_realm.setdefault(int(fields["realm_id"]), []).append(ev)
        return ev

    def update_event(eid, **fields):
        resolved.append((int(eid), fields))
        if fields.get("status") == "resolved":
            for rid, evs in active_by_realm.items():
                active_by_realm[rid] = [e for e in evs if int(e["id"]) != int(eid)]

    def update_fief(fid, **fields):
        fief_updates.append((int(fid), fields))

    engine.db.create_event.side_effect = create_event
    engine.db.update_event.side_effect = update_event
    engine.db.update_fief.side_effect = update_fief
    engine.db.update_world = MagicMock()
    engine.db.list_fiefs.return_value = [
        {"id": 1, "name": "A", "grain": 100, "frozen": False},
    ]
    engine.db.event_actions.return_value = []
    engine.db.get_fief.return_value = {"id": 1, "goods": 0}

    bot = MagicMock()
    with patch("app.scheduler.post_realm_public", new=AsyncMock()):
        await _maybe_post_world_catastrophe(bot, engine, world)

    assert (22, {"status": "resolved"}) in resolved
    # Дивергент (r2) и отсутствующая (r3) получают resolved-placeholder, не active.
    assert len(created) == 2
    assert {c["realm_id"] for c in created} == {2, 3}
    assert all(c["event_key"] == "bandit_night" for c in created)
    assert all(c["resolves_tick"] == 10 for c in created)
    assert all(c["status"] == "resolved" for c in created)
    assert fief_updates == []

    # Placeholder не попадает в active и не даёт fail при resolve poll.
    with patch("app.scheduler.post_realm_public", new=AsyncMock()) as announce:
        await _resolve_expired_catastrophes(bot, engine, r2)
        await _resolve_expired_catastrophes(bot, engine, r3)
    assert fief_updates == []
    announce.assert_not_called()


def test_join_fief_rejects_second_estate_on_continent():
    db = MagicMock()
    user = MagicMock(id=42, username="u", full_name="U", first_name="U")
    db.get_fief_by_user.return_value = None
    db.get_fief_by_user_world.return_value = {
        "id": 9,
        "realm_id": 1,
        "user_id": 42,
        "world_id": 1,
    }
    db._fetchone.return_value = {
        "id": 50,
        "realm_id": 2,
        "owner_fief_id": None,
        "tile_type": "field",
        "x": 1,
        "y": 1,
    }
    db.get_realm.return_value = {"id": 2, "width": 5, "height": 5, "world_id": 1}
    db.get_tiles.return_value = []

    engine = Engine(db)
    engine.ensure_user = MagicMock()

    with pytest.raises(ValueError, match="уже есть усадьба"):
        engine.join_fief(2, user, tile_id=50)
    db.create_fief.assert_not_called()
    db.get_fief_by_user_world.assert_called_once_with(42, 1)


_INCOMPLETE_MSG = "догоняет тик"


def _incomplete_world_db(*, caught_up: bool = False):
    """Две долины одного мира; при caught_up=False экономика отстаёт."""
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    world = _world(tick_index=3)
    r1 = _realm(
        1,
        tick_index=3,
        last_economy_tick=3 if caught_up else 2,
    )
    r2 = _realm(
        2,
        tick_index=3,
        last_economy_tick=3 if caught_up else 2,
    )
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = [r1, r2]
    db.realms_are_adjacent.return_value = True
    db.get_realm.side_effect = lambda rid: {1: r1, 2: r2}.get(int(rid), r1)
    return db, B, r1, r2


def test_incomplete_world_blocks_cross_valley_raid_caravan_pact():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    atk = {
        "id": 1,
        "realm_id": 1,
        "user_id": 100,
        "name": "A",
        "grain": 50,
        "goods": 20,
        "might": 10,
        "hungry": False,
        "actions": 1,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": 50,
        "frozen": False,
    }
    vic = dict(atk, id=2, realm_id=2, user_id=200, name="B", might=3, pact_id=None)
    pact = {"id": 50, "realm_id": 1, "name": "Север", "founder_fief_id": 1}
    invite = {
        "id": 9,
        "status": "open",
        "realm_id": 1,
        "pact_id": 50,
        "target_fief_id": 2,
        "expires_tick": 20,
    }

    fiefs = {1: atk, 2: vic}
    db.get_fief.side_effect = lambda fid: dict(fiefs[int(fid)])
    db.get_pact.return_value = pact
    db.pact_members.return_value = [atk]
    db.get_open_pact_invite.return_value = None
    db.get_pact_invite.return_value = invite
    db.last_raid_attacker_victim.return_value = None

    engine = Engine(db)
    engine.require_active_fief = MagicMock(return_value=atk)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])

    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.declare_raid(1, 2, 5)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.invite_to_pact(1, 2)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.accept_pact_invite(2, 9)


def test_incomplete_world_blocks_same_realm_raid():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    atk = {
        "id": 1,
        "realm_id": 1,
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
    db.get_fief.side_effect = lambda fid: atk if int(fid) == 1 else vic
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = []
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.require_active_fief = MagicMock(return_value=atk)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.declare_raid(1, 2, 5)
    engine._spend_action.assert_not_called()
    db.log_raid.assert_not_called()


def test_caught_up_world_allows_cross_valley_again():
    db, B, r1, r2 = _incomplete_world_db(caught_up=True)
    sender = {
        "id": 1,
        "realm_id": 1,
        "grain": 50,
        "goods": 10,
        "frozen": False,
        "name": "A",
        "user_id": 100,
        "pact_id": 50,
    }
    receiver = {
        "id": 2,
        "realm_id": 2,
        "grain": 5,
        "goods": 5,
        "frozen": False,
        "name": "B",
        "user_id": 200,
        "pact_id": None,
    }
    fiefs = {1: dict(sender), 2: dict(receiver)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def debit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    db.get_fief.side_effect = get_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.create_action_intent.return_value = {"id": 11, "kind": "caravan"}
    db.get_pact.return_value = {
        "id": 50,
        "realm_id": 1,
        "name": "Север",
        "founder_fief_id": 1,
    }
    db.pact_members.return_value = [fiefs[1]]
    db.get_open_pact_invite.return_value = None
    db.create_pact_invite.return_value = {"id": 9}

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._format_raid_deadline = MagicMock(return_value="-")

    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    assert "B" in result.dm_text
    assert fiefs[1]["grain"] == 40
    assert fiefs[2]["grain"] == 5

    invite = engine.invite_to_pact(1, 2)
    assert invite["id"] == 9


def test_incomplete_same_realm_raid_skips_foreign_interceptor_spend():
    """Локальный набег при incomplete не тратит силу перехватчика из другой долины."""
    db, B, _r1, _r2 = _incomplete_world_db(caught_up=False)
    atk = {
        "id": 1,
        "realm_id": 1,
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
    vic = {
        "id": 2,
        "realm_id": 1,
        "user_id": 200,
        "name": "B",
        "grain": 40,
        "goods": 10,
        "might": 3,
        "hungry": False,
        "actions": 1,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": 50,
    }
    foreign = {
        "id": 3,
        "realm_id": 2,
        "user_id": 300,
        "name": "C",
        "grain": 40,
        "goods": 10,
        "might": B.INTERCEPT_MIGHT + 2,
        "hungry": False,
        "cover_allies": True,
        "pact_id": 50,
    }
    fiefs = {1: dict(atk), 2: dict(vic), 3: dict(foreign)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    def debit_fief_resources(fid, **amounts):
        row = fiefs[int(fid)]
        for col, amt in amounts.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = [fiefs[2], fiefs[3]]
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    # Окно действий снято, чтобы проверить skip чужого перехватчика при incomplete.
    engine._require_action_window = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    # incomplete: ночной resolve не стартует; чужой перехватчик не тратится.
    foreign_might_before = fiefs[3]["might"]
    engine.world_tick_incomplete = MagicMock(return_value=True)
    report = engine.resolve_pending_raids(1, 3)
    assert report.resolved_count == 0
    assert fiefs[3]["might"] == foreign_might_before
    picked = engine._pick_raid_interceptor(fiefs[2], incomplete_world=True)
    assert picked is None or int(picked["id"]) != 3


def test_incomplete_same_realm_raid_spends_local_cover_allies_interceptor():
    """Локальный cover_allies при incomplete перехватывает; чужие долины не тратятся."""
    db, B, _r1, _r2 = _incomplete_world_db(caught_up=False)
    atk = {
        "id": 1,
        "realm_id": 1,
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
    vic = {
        "id": 2,
        "realm_id": 1,
        "user_id": 200,
        "name": "B",
        "grain": 40,
        "goods": 10,
        "might": 3,
        "hungry": False,
        "actions": 1,
        "shield_until_tick": None,
        "last_raid_tick": None,
        "patrol_until_tick": None,
        "pact_id": 50,
    }
    local = {
        "id": 4,
        "realm_id": 1,
        "user_id": 400,
        "name": "D",
        "grain": 40,
        "goods": 10,
        "might": B.INTERCEPT_MIGHT + 3,
        "hungry": False,
        "cover_allies": True,
        "pact_id": 50,
    }
    foreign = {
        "id": 3,
        "realm_id": 2,
        "user_id": 300,
        "name": "C",
        "grain": 40,
        "goods": 10,
        "might": B.INTERCEPT_MIGHT + 2,
        "hungry": False,
        "cover_allies": True,
        "pact_id": 50,
    }
    fiefs = {1: dict(atk), 2: dict(vic), 3: dict(foreign), 4: dict(local)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    def debit_fief_resources(fid, **amounts):
        row = fiefs[int(fid)]
        for col, amt in amounts.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.last_raid_attacker_victim.return_value = None
    # Чужой союзник первым: incomplete должен его пропустить и взять локального.
    db.pact_members.return_value = [fiefs[2], fiefs[3], fiefs[4]]
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine._require_action_window = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    # incomplete: resolve не идёт; выбор перехватчика пропускает чужую долину.
    picked = engine._pick_raid_interceptor(fiefs[2], incomplete_world=True)
    assert picked is not None
    assert int(picked["id"]) == 4
    engine.world_tick_incomplete = MagicMock(return_value=True)
    report = engine.resolve_pending_raids(1, 3)
    assert report.resolved_count == 0
    assert fiefs[4]["might"] == local["might"]
    assert fiefs[3]["might"] == foreign["might"]


def test_incomplete_world_blocks_declare_caravan():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    sender = {
        "id": 1,
        "realm_id": 1,
        "grain": 50,
        "goods": 10,
        "name": "A",
        "user_id": 100,
        "frozen": False,
    }
    receiver = {
        "id": 2,
        "realm_id": 1,
        "grain": 5,
        "goods": 5,
        "name": "B",
        "user_id": 200,
        "frozen": False,
    }
    fiefs = {1: dict(sender), 2: dict(receiver)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    db.get_fief.side_effect = get_fief
    db.create_action_intent.return_value = {"id": 11}

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])

    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.declare_caravan(1, 2, B.RES_GRAIN, 5)
    db.create_action_intent.assert_not_called()
    assert fiefs[1]["grain"] == 50


def test_incomplete_leave_pact_blocks_cross_valley_dissolve():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    leaver = {
        "id": 1,
        "realm_id": 1,
        "user_id": 100,
        "name": "A",
        "pact_id": 50,
    }
    other = {
        "id": 2,
        "realm_id": 2,
        "user_id": 200,
        "name": "B",
        "pact_id": 50,
    }
    fiefs = {1: dict(leaver), 2: dict(other)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.get_pact.return_value = {
        "id": 50,
        "realm_id": 1,
        "name": "Север",
        "founder_fief_id": 1,
    }
    db.pact_members.return_value = [fiefs[1], fiefs[2]]

    engine = Engine(db)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.leave_pact(1)
    assert fiefs[1]["pact_id"] == 50
    db.dissolve_pact.assert_not_called()


def test_stuck_economy_after_catch_up_closes_play_without_new_tick():
    """Crash после fan-out: следующий вызов ставит play, не двигает tick_index."""
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    r1 = _realm(1, tick_index=5, last_economy_tick=5)
    r2 = _realm(2, tick_index=5, last_economy_tick=5)
    world = _world(tick_index=5, tick_phase="economy", pending_minor_key=None)
    chain = [r1, r2]
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def update_world(_wid, **fields):
        world.update(fields)

    db.update_world.side_effect = update_world
    db.sync_realms_clock_from_world = MagicMock()

    engine = Engine(db)
    engine.run_realm_tick = MagicMock(
        side_effect=AssertionError("не должен стартовать новый fan-out")
    )

    with patch("app.services.world_tick.roll_minor_event", return_value="fog"):
        result = engine.run_world_tick(1)

    assert result["incomplete"] is False
    assert result["resumed"] is True
    assert world["tick_index"] == 5
    assert world["tick_phase"] == "play"
    assert world["pending_minor_key"] == "fog"
    engine.run_realm_tick.assert_not_called()


def test_economy_phase_rejects_spend_even_when_caught_up():
    db, _B, r1, r2 = _incomplete_world_db(caught_up=True)
    db.get_world.return_value = _world(tick_index=3, tick_phase="economy")
    db.get_or_create_world.return_value = db.get_world.return_value
    db.get_user.return_value = {"last_realm_id": 1}
    db.list_fiefs_by_user.return_value = [{"id": 1, "realm_id": 1}]
    db.spend_fief_action.return_value = {
        "id": 1,
        "actions": 0,
        "frozen": False,
        "user_id": 10,
        "realm_id": 1,
    }

    engine = Engine(db)
    fief = {
        "id": 1,
        "actions": 1,
        "frozen": False,
        "user_id": 10,
        "realm_id": 1,
    }
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine._spend_action(fief)
    db.spend_fief_action.assert_not_called()


def test_world_tick_sets_play_after_successful_catch_up():
    db = MagicMock()
    db.transaction.return_value = nullcontext()
    r1 = _realm(1, last_economy_tick=0)
    r2 = _realm(2, last_economy_tick=0)
    world = _world(tick_index=0, pending_minor_key=None, tick_phase="play")
    chain = [r1, r2]

    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def sync(_wid):
        for r in chain:
            r["tick_index"] = world["tick_index"]
            r["day_number"] = world["day_number"]

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
    engine.run_realm_tick = MagicMock(
        side_effect=lambda rid, tick_slot=None, *, advance_clock=True: {
            "realm_id": int(rid),
            "digest": f"d{rid}",
            "chat_id": -rid,
        }
    )

    with patch("app.services.world_tick.roll_minor_event", return_value="fog"):
        result = engine.run_world_tick(1, tick_slot=0)

    assert result["incomplete"] is False
    assert world["tick_phase"] == "play"
    assert r1["last_economy_tick"] == 1
    assert r2["last_economy_tick"] == 1


def test_tick_phase_migration_default_play():
    from app.database import Database

    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    # Минимальный happy-path для _ensure_world_schema после CREATE/ALTER.
    cursor.fetchone.side_effect = [
        (1,),  # existing world id
        (0,),  # need_attach count
        None,  # no further rows needed for optional branches
    ]
    cursor.fetchall.return_value = []

    try:
        db._ensure_world_schema()
    except Exception:
        # Схема тянет много веток; достаточно проверить ADD COLUMN.
        pass

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert any(
        "ADD COLUMN IF NOT EXISTS tick_phase" in sql and "DEFAULT 'play'" in sql
        for sql in executed
    )


def test_needs_economy_wake_uses_normalize():
    from app.domain.tick_pipeline import needs_economy_wake

    assert needs_economy_wake({"tick_phase": "economy"}) is True
    assert needs_economy_wake({"tick_phase": "play"}) is False
    assert needs_economy_wake({}) is False
    assert needs_economy_wake({"tick_phase": None}) is False
    assert needs_economy_wake({"tick_phase": "unknown"}) is False


@pytest.mark.asyncio
async def test_scheduler_economy_wake_calls_run_world_tick_without_slot():
    """economy + caught up: будит run_world_tick с tick_slot=None, даже если слот due."""
    from app.scheduler import _scheduler_tick

    world = _world(
        tick_index=5,
        tick_phase="economy",
        last_tick_local_date="2026-07-17",
        last_tick_slot=0,
    )
    engine = MagicMock()
    engine.default_world.return_value = world
    engine.world.return_value = world
    engine.realms_of_world.return_value = []
    engine.world_tick_incomplete.return_value = False
    engine.run_world_tick.return_value = {
        "world_id": 1,
        "realms": [],
        "resumed": True,
        "incomplete": False,
    }

    bot = MagicMock()
    with (
        patch("app.scheduler.get_engine", return_value=engine),
        patch("app.scheduler.due_tick_slot", return_value=1),
        patch("app.scheduler._maybe_post_world_catastrophe", new=AsyncMock()),
        patch("app.scheduler.announce_pending_patches", new=AsyncMock()),
    ):
        await _scheduler_tick(bot)

    engine.run_world_tick.assert_called_once_with(1, tick_slot=None)
