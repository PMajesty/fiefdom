"""Синхронизация континента: тик, катастрофы, набеги, вторая усадьба."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
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
    db.clear_world_force_tick_votes = MagicMock(return_value=0)

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
        patch("app.engine.roll_minor_event", return_value="fog"),
        pytest.raises(RuntimeError, match="crash after world advance"),
    ):
        engine.run_world_tick(1, tick_slot=0)

    assert world["tick_index"] == 0
    assert world["day_number"] == 1
    assert r1["tick_index"] == 0
    assert r1["day_number"] == 1
    engine.run_realm_tick.assert_not_called()


def _assert_clock_advance_clears_votes_in_same_tx(*, forced: bool):
    """update_world, sync и clear_votes в одной transaction() при любом advance."""
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

    def clear_votes(_wid):
        ops.append(("clear_votes", depth["n"]))
        return 0

    def update_realm(rid, **fields):
        for r in chain:
            if int(r["id"]) == int(rid):
                r.update(fields)

    db.update_world.side_effect = update_world
    db.sync_realms_clock_from_world.side_effect = sync
    db.clear_world_force_tick_votes.side_effect = clear_votes
    db.update_realm.side_effect = update_realm
    db.list_world_force_tick_votes.return_value = [
        {"fief_id": 10},
        {"fief_id": 11},
    ]

    engine = Engine(db)
    engine.force_tick_eligible_fiefs_world = MagicMock(
        return_value=[{"id": 10}, {"id": 11}]
    )
    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -1}
    )

    with patch("app.engine.roll_minor_event", return_value=None):
        if forced:
            engine.run_world_tick(1, forced=True)
        else:
            engine.run_world_tick(1, tick_slot=0)

    advance = [o for o in ops if o[0] in ("update_world", "sync", "clear_votes")]
    assert advance[:3] == [
        ("update_world", 1),
        ("sync", 1),
        ("clear_votes", 1),
    ]


def test_forced_clock_advance_clears_votes_inside_same_transaction():
    _assert_clock_advance_clears_votes_in_same_tx(forced=True)


def test_scheduled_clock_advance_clears_votes_inside_same_transaction():
    _assert_clock_advance_clears_votes_in_same_tx(forced=False)


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
        patch("app.scheduler.announce_realm", new=fake_post),
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

    with patch("app.scheduler.announce_realm", new=AsyncMock()):
        await _maybe_post_world_catastrophe(bot, engine, world_due)

    assert len(created) == 1
    assert created[0]["realm_id"] == 2
    assert created[0]["event_key"] == "bandit_night"
    assert engine.db.update_world.called


@pytest.mark.asyncio
async def test_catastrophe_heals_divergent_active_keys_to_canonical_wave():
    """Разные активные ключи не должны вечно стопорить fan-out."""
    engine = MagicMock()
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
        patch("app.scheduler.announce_realm", new=AsyncMock()),
        patch("app.scheduler.pick_catastrophe", return_value="cattle_plague"),
        patch("app.scheduler.next_catastrophe_delay_ticks", return_value=7),
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
    engine = MagicMock()
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
    with patch("app.scheduler.announce_realm", new=AsyncMock()):
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

    engine = MagicMock()
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
    with patch("app.scheduler.announce_realm", new=AsyncMock()):
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
    with patch("app.scheduler.announce_realm", new=AsyncMock()) as announce:
        await _resolve_expired_catastrophes(bot, engine, r2)
        await _resolve_expired_catastrophes(bot, engine, r3)
    assert fief_updates == []
    announce.assert_not_called()


def test_join_fief_rejects_second_estate_on_continent():
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

    with pytest.raises(ValueError, match="уже есть усадьба"):
        engine.join_fief(2, user, tile_id=50)
    db.create_fief.assert_not_called()


_INCOMPLETE_MSG = "догоняет тик"


def _incomplete_world_db(*, caught_up: bool = False):
    """Две долины одного мира; при caught_up=False экономика отстаёт."""
    from app import balance as B

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


def test_incomplete_world_blocks_cross_valley_raid_send_trade_pact():
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
    trade = {
        "id": 7,
        "status": "open",
        "realm_id": 1,
        "offerer_fief_id": 1,
        "target_fief_id": None,
        "give_res": B.RES_GRAIN,
        "give_amt": 5,
        "want_res": B.RES_GOODS,
        "want_amt": 3,
        "expires_tick": 20,
    }
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
    db.get_trade.return_value = trade
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
        engine.raid(1, 2, might=5)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.send_resources(1, 2, B.RES_GRAIN, 10)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.accept_trade(2, 7)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.post_trade(
            1, B.RES_GRAIN, 5, B.RES_GOODS, 3, target_fief_id=2
        )
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.invite_to_pact(1, 2)
    with pytest.raises(ValueError, match=_INCOMPLETE_MSG):
        engine.accept_pact_invite(2, 9)


def test_incomplete_world_allows_same_realm_raid():
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
        result = engine.raid(1, 2, might=5)

    assert result.via_portal is False
    db.log_raid.assert_called_once()


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
    db.pact_members.return_value = [fiefs[1]]
    db.get_open_pact_invite.return_value = None
    db.create_pact_invite.return_value = {"id": 9}

    trade = {
        "id": 7,
        "status": "open",
        "realm_id": 1,
        "offerer_fief_id": 1,
        "target_fief_id": None,
        "give_res": B.RES_GRAIN,
        "give_amt": 5,
        "want_res": B.RES_GOODS,
        "want_amt": 3,
        "expires_tick": 20,
    }
    db.get_trade.return_value = trade
    db.claim_open_trade.return_value = dict(trade)

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])

    msg = engine.send_resources(1, 2, B.RES_GRAIN, 10)
    assert "B" in msg
    assert fiefs[1]["grain"] == 40
    assert fiefs[2]["grain"] == 15

    invite = engine.invite_to_pact(1, 2)
    assert invite["id"] == 9

    msg = engine.accept_trade(2, 7)
    assert msg.startswith("Сделка")


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

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = [fiefs[2], fiefs[3]]
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    foreign_might_before = fiefs[3]["might"]
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
    ) as resolve:
        result = engine.raid(1, 2, might=5)

    assert result.via_portal is False
    assert result.intercept_applied is False
    assert result.interceptor_fief_id is None
    assert resolve.call_args.kwargs["intercept"] is False
    assert fiefs[3]["might"] == foreign_might_before
    for call in db.update_fief.call_args_list:
        assert call.args[0] != 3


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

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.last_raid_attacker_victim.return_value = None
    # Чужой союзник первым: incomplete должен его пропустить и взять локального.
    db.pact_members.return_value = [fiefs[2], fiefs[3], fiefs[4]]
    db.update_realm = MagicMock()

    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine._spend_action = MagicMock()
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.fief_prod = MagicMock(
        return_value=MagicMock(defense=0, grain=1, goods=1)
    )
    engine.barn_level = MagicMock(return_value=0)

    local_might_before = fiefs[4]["might"]
    foreign_might_before = fiefs[3]["might"]
    with patch(
        "app.engine.resolve_raid",
        return_value=MagicMock(
            public_line="Набег!",
            success=False,
            might_lost=1,
            grain_stolen=0,
            goods_stolen=0,
            intercept_applied=True,
        ),
    ) as resolve:
        result = engine.raid(1, 2, might=5)

    assert result.via_portal is False
    assert result.intercept_applied is True
    assert result.interceptor_fief_id == 4
    assert resolve.call_args.kwargs["intercept"] is True
    assert fiefs[4]["might"] == local_might_before - B.INTERCEPT_MIGHT
    assert fiefs[3]["might"] == foreign_might_before
    for call in db.update_fief.call_args_list:
        assert call.args[0] != 3


def test_incomplete_world_allows_open_market_post_trade():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    seller = {
        "id": 1,
        "realm_id": 1,
        "grain": 50,
        "goods": 10,
        "name": "A",
        "user_id": 100,
    }
    fiefs = {1: dict(seller)}

    def get_fief(fid):
        return dict(fiefs[int(fid)])

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.create_trade.return_value = {"id": 11}

    engine = Engine(db)
    engine.collect_for_fief = MagicMock()

    msg = engine.post_trade(1, B.RES_GRAIN, 5, B.RES_GOODS, 3, target_fief_id=None)
    assert msg.startswith("Лот #11")
    assert fiefs[1]["grain"] == 45
    db.create_trade.assert_called_once()


def test_incomplete_world_allows_cast_force_tick_vote():
    db, B, r1, r2 = _incomplete_world_db(caught_up=False)
    fief = {
        "id": 1,
        "realm_id": 1,
        "user_id": 100,
        "frozen": False,
        "name": "A",
    }
    db.get_fief.return_value = fief
    db.add_force_tick_vote.return_value = True

    engine = Engine(db)
    engine.require_active_fief = MagicMock(return_value=fief)
    engine.force_tick_progress = MagicMock(
        return_value={
            "eligible": 2,
            "votes": 1,
            "needed": 2,
            "available": True,
            "vote_fief_ids": {1},
        }
    )
    engine.run_world_tick = MagicMock()

    result = engine.cast_force_tick_vote(1)
    assert result["status"] == "voted"
    db.add_force_tick_vote.assert_called_once_with(1, 1)
    engine.run_world_tick.assert_not_called()


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
