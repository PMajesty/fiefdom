"""Голосование за досрочный (бесплатный) тик."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app import balance as B
from app.domain.rumors import DailyRumorBundle
from app.engine import Engine
from app.handlers.shared import home_kb


def test_force_tick_votes_needed_curve():
    assert B.FORCE_TICK_MIN_PLAYERS == 2
    assert B.force_tick_votes_needed(0) == 2
    assert B.force_tick_votes_needed(1) == 2
    assert B.force_tick_votes_needed(2) == 2
    assert B.force_tick_votes_needed(3) == 3
    assert B.force_tick_votes_needed(4) == 4
    assert B.force_tick_votes_needed(5) == 5
    assert B.force_tick_votes_needed(10) == 10


def _realm(**overrides):
    data = {
        "id": 1,
        "world_id": 1,
        "chain_index": 0,
        "title": "Долина",
        "chat_id": -100,
        "day_number": 5,
        "timezone": "Europe/Moscow",
        "pending_raid_lines": [],
        "active_minor_key": None,
        "active_minor_until": None,
        "pending_minor_key": "",
        "tick_index": 0,
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 0,
        "forced_tick_count": 0,
        "last_economy_tick": 0,
    }
    data.update(overrides)
    return data


def _world(**overrides):
    data = {
        "id": 1,
        "name": "Континент",
        "day_number": 5,
        "timezone": "Europe/Moscow",
        "pending_raid_lines": [],
        "active_minor_key": None,
        "active_minor_until": None,
        "pending_minor_key": "",
        "tick_index": 0,
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 0,
        "forced_tick_count": 0,
        "next_catastrophe_tick": 10,
        "next_catastrophe_key": None,
        "last_catastrophe_key": None,
    }
    data.update(overrides)
    return data


def _fief(fid: int, user_id: int, **overrides):
    data = {
        "id": fid,
        "realm_id": 1,
        "user_id": user_id,
        "name": f"Усадьба{fid}",
        "grain": 50,
        "goods": 20,
        "might": 5,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
        "actions": 1,
        "hungry": False,
        "frozen": False,
        "patrol_until": None,
    }
    data.update(overrides)
    return data


def _engine_with_votes(fiefs: list[dict], votes: set[int] | None = None):
    db = MagicMock()
    realm = _realm()
    world = _world()
    vote_set = set(votes or [])

    db.get_realm.return_value = realm
    db.list_fiefs.return_value = fiefs
    db.list_realms_by_chain.return_value = [realm]
    db.get_or_create_world.return_value = world
    db.get_world.return_value = world
    db.transaction.return_value = nullcontext()
    db.list_open_trades.return_value = []
    db.list_expired_open_trades.return_value = []
    db.get_active_events.return_value = []
    db.raids_since_tick.return_value = []
    db.sync_realms_clock_from_world = MagicMock()
    db.fief_tiles.return_value = [
        {
            "x": 0,
            "y": 0,
            "tile_type": "field",
            "owner_fief_id": fiefs[0]["id"],
            "building": "farm",
            "building_level": 1,
            "is_core": True,
            "is_overgrown": False,
        }
    ]

    def get_fief(fid):
        for f in fiefs:
            if f["id"] == fid:
                return f
        return None

    db.get_fief.side_effect = get_fief
    db.get_user.side_effect = lambda uid: {
        "telegram_id": uid,
        "last_realm_id": 1,
    }
    db.list_fiefs_by_user.side_effect = lambda uid: [
        f for f in fiefs if int(f["user_id"]) == int(uid)
    ]

    def list_world_votes(world_id):
        return [{"world_id": world_id, "realm_id": 1, "fief_id": fid} for fid in sorted(vote_set)]

    def add_vote(realm_id, fief_id):
        if fief_id in vote_set:
            return False
        vote_set.add(fief_id)
        return True

    def clear_world_votes(world_id):
        n = len(vote_set)
        vote_set.clear()
        return n

    def update_realm(rid, **fields):
        realm.update(fields)

    def update_world(wid, **fields):
        world.update(fields)
        # зеркало часов на долину (как sync)
        for k in (
            "day_number",
            "tick_index",
            "last_tick_slot",
            "last_tick_local_date",
            "forced_tick_count",
            "active_minor_key",
            "pending_minor_key",
        ):
            if k in fields:
                realm[k] = fields[k]

    def update_fief(fid, **fields):
        f = get_fief(fid)
        if f:
            f.update(fields)

    db.list_world_force_tick_votes.side_effect = list_world_votes
    db.list_force_tick_votes.side_effect = lambda rid: list_world_votes(1)
    db.add_force_tick_vote.side_effect = add_vote
    db.clear_world_force_tick_votes.side_effect = clear_world_votes
    db.clear_force_tick_votes.side_effect = clear_world_votes
    db.update_realm.side_effect = update_realm
    db.update_world.side_effect = update_world
    db.update_fief.side_effect = update_fief

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])
    engine._prepare_tick_minor = MagicMock(return_value=None)
    engine._realm_farm_mult = MagicMock(return_value=1.0)
    engine._active_cattle_plague = MagicMock(return_value=None)
    engine._roll_day_rumors = MagicMock(return_value=DailyRumorBundle())
    engine._upcoming_event_hints = MagicMock(return_value=[])
    engine._sunday_extra = MagicMock(return_value=None)
    return engine, realm, vote_set


def test_force_tick_too_few_players():
    fiefs = [_fief(10, 1001)]
    engine, _, _ = _engine_with_votes(fiefs)
    result = engine.cast_force_tick_vote(10)
    assert result["status"] == "too_few"
    assert result["progress"]["available"] is False


def test_force_tick_vote_progress_excludes_frozen():
    fiefs = [
        _fief(10, 1001),
        _fief(11, 1002),
        _fief(12, 1003, frozen=True),
    ]
    engine, _, _ = _engine_with_votes(fiefs, votes={10})
    progress = engine.force_tick_progress(1)
    assert progress["eligible"] == 2
    assert progress["needed"] == 2
    assert progress["votes"] == 1
    assert progress["available"] is True


def test_force_tick_partial_then_force_free_slot():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs)

    first = engine.cast_force_tick_vote(10)
    assert first["status"] == "voted"
    assert first["progress"]["votes"] == 1
    assert realm["day_number"] == 5
    assert realm["last_tick_slot"] == 0
    assert 10 in votes

    second = engine.cast_force_tick_vote(11)
    assert second["status"] == "forced"
    assert realm["day_number"] == 6
    assert realm["forced_tick_count"] == 1
    # Бесплатный тик: слот расписания не сдвинут.
    assert realm["last_tick_slot"] == 0
    assert realm["last_tick_local_date"] == date(2026, 7, 16)
    assert votes == set()
    assert second["tick"]["digest"]


def test_forced_tick_ignores_tick_slot_arg():
    """forced=True не закрывает плановый слот даже если передан tick_slot."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, _votes = _engine_with_votes(fiefs, votes={10, 11})
    world = engine.db.get_world.return_value
    with patch("app.engine.roll_minor_event", return_value=None):
        engine.run_world_tick(1, tick_slot=1, forced=True)
    assert world["tick_index"] == 1
    assert world["forced_tick_count"] == 1
    assert world["last_tick_slot"] == 0
    assert realm["last_tick_slot"] == 0


def test_force_tick_requires_all_eligible_players():
    fiefs = [_fief(10, 1001), _fief(11, 1002), _fief(12, 1003)]
    engine, realm, votes = _engine_with_votes(fiefs)

    first = engine.cast_force_tick_vote(10)
    assert first["status"] == "voted"
    assert first["progress"]["needed"] == 3
    second = engine.cast_force_tick_vote(11)
    assert second["status"] == "voted"
    assert second["progress"]["votes"] == 2
    assert realm["day_number"] == 5
    assert 10 in votes and 11 in votes

    third = engine.cast_force_tick_vote(12)
    assert third["status"] == "forced"
    assert realm["day_number"] == 6
    assert votes == set()


def test_force_tick_one_realm_cannot_force_alone():
    """Голоса одной долины не форсят тик, пока другие долины не согласны."""
    realm_a = _realm(id=1, chain_index=0, title="Альфа")
    realm_b = _realm(id=2, chain_index=1, title="Бета")
    fiefs_a = [_fief(10, 1001, realm_id=1), _fief(11, 1002, realm_id=1)]
    fiefs_b = [_fief(20, 2001, realm_id=2), _fief(21, 2002, realm_id=2)]
    all_fiefs = fiefs_a + fiefs_b

    engine, realm, votes = _engine_with_votes(all_fiefs)
    engine.db.list_realms_by_chain.return_value = [realm_a, realm_b]
    engine.db.get_realm.side_effect = lambda rid: {
        1: realm_a,
        2: realm_b,
    }.get(int(rid))
    engine.db.list_fiefs.side_effect = lambda rid: {
        1: fiefs_a,
        2: fiefs_b,
    }.get(int(rid), [])

    assert engine.cast_force_tick_vote(10)["status"] == "voted"
    assert engine.cast_force_tick_vote(11)["status"] == "voted"
    progress = engine.force_tick_progress(1)
    assert progress["eligible"] == 4
    assert progress["needed"] == 4
    assert progress["votes"] == 2
    assert realm["day_number"] == 5
    assert votes == {10, 11}

    assert engine.cast_force_tick_vote(20)["status"] == "voted"
    third = engine.cast_force_tick_vote(21)
    assert third["status"] == "forced"
    assert realm["day_number"] == 6
    assert votes == set()


def test_force_tick_already_voted():
    fiefs = [_fief(10, 1001), _fief(11, 1002), _fief(12, 1003)]
    engine, _, _ = _engine_with_votes(fiefs, votes={10})
    result = engine.cast_force_tick_vote(10)
    assert result["status"] == "already"
    assert result["progress"]["votes"] == 1


def test_force_tick_keeps_votes_if_tick_raises_before_advance():
    """Инвариант: голоса живут, пока forced-сдвиг часов не зафиксирован.

    cast_force_tick_vote больше не clear'ит до run_world_tick; если тик
    падает до advance, кворум сохраняется.
    """
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10})
    engine.run_world_tick = MagicMock(side_effect=RuntimeError("boom before tick"))

    with pytest.raises(RuntimeError, match="boom before tick"):
        engine.cast_force_tick_vote(11)

    assert votes == {10, 11}
    assert realm["day_number"] == 5
    assert realm["tick_index"] == 0
    engine.run_world_tick.assert_called_once_with(1, forced=True)


def test_forced_tick_clears_votes_with_clock_advance_even_if_incomplete():
    """После успешного forced-advance голоса сброшены; resume без повторного голосования."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10, 11})
    world = engine.db.get_world.return_value
    engine.run_realm_tick = MagicMock(
        side_effect=RuntimeError("realm economy failed")
    )

    with patch("app.engine.roll_minor_event", return_value=None):
        result = engine.run_world_tick(1, forced=True)

    assert result["incomplete"] is True
    assert world["tick_index"] == 1
    assert world["forced_tick_count"] == 1
    assert votes == set()
    assert realm.get("last_economy_tick") == 0


def test_forced_tick_skips_second_advance_without_mandate():
    """Параллельный forced без голосов не двигает часы повторно."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes=set())
    assert votes == set()

    result = engine.run_world_tick(1, forced=True)
    assert result.get("forced_skipped") is True
    assert realm["day_number"] == 5
    assert realm["tick_index"] == 0


def test_cast_maps_forced_skipped_to_voted_with_progress():
    """forced_skipped не выдаёт status forced; прогресс голосов обновлён."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10})
    engine.run_world_tick = MagicMock(
        return_value={
            "world_id": 1,
            "realms": [],
            "digest": None,
            "chat_id": None,
            "resumed": False,
            "incomplete": False,
            "forced_skipped": True,
        }
    )

    result = engine.cast_force_tick_vote(11)

    assert result["status"] == "voted"
    assert result["progress"]["votes"] == 2
    assert result["progress"]["needed"] == 2
    assert "tick" not in result
    assert votes == {10, 11}
    assert realm["day_number"] == 5
    engine.run_world_tick.assert_called_once_with(1, forced=True)


def test_cast_maps_forced_skipped_to_already_with_progress():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, _, votes = _engine_with_votes(fiefs, votes={10, 11})
    engine.run_world_tick = MagicMock(
        return_value={
            "world_id": 1,
            "realms": [],
            "digest": None,
            "chat_id": None,
            "resumed": False,
            "incomplete": False,
            "forced_skipped": True,
        }
    )

    result = engine.cast_force_tick_vote(10)

    assert result["status"] == "already"
    assert result["progress"]["votes"] == 2
    assert result["progress"]["needed"] == 2
    assert votes == {10, 11}


def test_cast_quorum_during_incomplete_does_not_claim_forced():
    """При догоне экономики кворум не форсирует новый день и не сжигает голоса."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10})
    world = engine.db.get_world.return_value
    world["tick_index"] = 3
    world["day_number"] = 8
    world["forced_tick_count"] = 1
    realm["tick_index"] = 3
    realm["day_number"] = 8
    realm["last_economy_tick"] = 2
    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -100}
    )

    result = engine.cast_force_tick_vote(11)

    assert result["status"] == "voted"
    assert world["tick_index"] == 3
    assert world["day_number"] == 8
    assert world["forced_tick_count"] == 1
    assert votes == {10, 11}
    assert realm["last_economy_tick"] == 3


def test_scheduled_tick_clears_votes_without_forced_counter():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10, 11})
    engine.run_realm_tick(1, tick_slot=1)
    assert realm["day_number"] == 6
    assert realm["last_tick_slot"] == 1
    assert realm.get("forced_tick_count", 0) == 0
    assert votes == set()


def test_scheduled_advance_clears_votes_before_resume_no_stale_quorum():
    """Scheduled advance сбрасывает кворум в TX; resume не даёт бесплатный forced-день."""
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10, 11})
    world = engine.db.get_world.return_value
    engine.run_realm_tick = MagicMock(
        side_effect=RuntimeError("realm economy failed")
    )

    with patch("app.engine.roll_minor_event", return_value=None):
        first = engine.run_world_tick(1, tick_slot=1)

    assert first["incomplete"] is True
    assert first["resumed"] is False
    assert world["tick_index"] == 1
    assert world["day_number"] == 6
    assert world.get("forced_tick_count", 0) == 0
    assert votes == set()
    assert realm.get("last_economy_tick") == 0

    engine.run_realm_tick = MagicMock(
        return_value={"realm_id": 1, "digest": "d", "chat_id": -100}
    )
    with patch("app.engine.roll_minor_event", return_value=None):
        resumed = engine.run_world_tick(1)

    assert resumed["resumed"] is True
    assert resumed["incomplete"] is False
    assert world["tick_index"] == 1
    assert world["day_number"] == 6
    assert votes == set()
    assert realm.get("last_economy_tick") == 1

    # Без новых голосов forced не двигает день повторно (нет stale quorum).
    skipped = engine.run_world_tick(1, forced=True)
    assert skipped.get("forced_skipped") is True
    assert world["tick_index"] == 1
    assert world["day_number"] == 6
    assert world.get("forced_tick_count", 0) == 0


def test_home_kb_shows_force_tick_button():
    kb = home_kb(7, "Рынок", "mkt:7", force_tick_progress=(1, 2))
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Тик сейчас (1/2)" in labels
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "ftv:7" in datas


def test_home_kb_hides_force_tick_without_progress():
    kb = home_kb(7, "Рынок", "mkt:7")
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any(t.startswith("Тик сейчас") for t in labels)

def test_force_tick_status_line():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, _, _ = _engine_with_votes(fiefs, votes={10})
    assert engine.force_tick_status_line(1) == "Голоса за тик сейчас: 1/2"
    solo, _, _ = _engine_with_votes([_fief(10, 1001)])
    assert solo.force_tick_status_line(1) is None
