"""Голосование за досрочный (бесплатный) тик."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

from app import balance as B
from app.engine import Engine
from app.handlers.shared import more_menu_kb


def test_force_tick_votes_needed_curve():
    assert B.FORCE_TICK_MIN_PLAYERS == 2
    assert B.force_tick_votes_needed(0) == 2
    assert B.force_tick_votes_needed(1) == 2
    assert B.force_tick_votes_needed(2) == 2
    assert B.force_tick_votes_needed(3) == 2
    assert B.force_tick_votes_needed(4) == 3
    assert B.force_tick_votes_needed(5) == 4
    assert B.force_tick_votes_needed(6) == 5
    assert B.force_tick_votes_needed(7) == 5
    assert B.force_tick_votes_needed(8) == 6


def _realm(**overrides):
    data = {
        "id": 1,
        "title": "Долина",
        "chat_id": -100,
        "day_number": 5,
        "timezone": "Europe/Moscow",
        "pending_raid_lines": [],
        "active_minor_key": None,
        "active_minor_until": None,
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 0,
        "forced_tick_count": 0,
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
    vote_set = set(votes or [])

    db.get_realm.return_value = realm
    db.list_fiefs.return_value = fiefs
    db.transaction.return_value = nullcontext()
    db.list_open_trades.return_value = []
    db.get_active_events.return_value = []
    db.raids_since.return_value = []
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

    def list_votes(realm_id):
        return [{"realm_id": realm_id, "fief_id": fid} for fid in sorted(vote_set)]

    def add_vote(realm_id, fief_id):
        if fief_id in vote_set:
            return False
        vote_set.add(fief_id)
        return True

    def clear_votes(realm_id):
        n = len(vote_set)
        vote_set.clear()
        return n

    def update_realm(rid, **fields):
        realm.update(fields)

    def update_fief(fid, **fields):
        f = get_fief(fid)
        if f:
            f.update(fields)

    db.list_force_tick_votes.side_effect = list_votes
    db.add_force_tick_vote.side_effect = add_vote
    db.clear_force_tick_votes.side_effect = clear_votes
    db.update_realm.side_effect = update_realm
    db.update_fief.side_effect = update_fief

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])
    engine._prepare_tick_minor = MagicMock(return_value=(None, None))
    engine._realm_farm_mult = MagicMock(return_value=1.0)
    engine._drought_mitigated_fief_ids = MagicMock(return_value=set())
    engine._rumor_snapshots = MagicMock(return_value=[])
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


def test_force_tick_already_voted():
    fiefs = [_fief(10, 1001), _fief(11, 1002), _fief(12, 1003)]
    engine, _, _ = _engine_with_votes(fiefs, votes={10})
    result = engine.cast_force_tick_vote(10)
    assert result["status"] == "already"
    assert result["progress"]["votes"] == 1


def test_scheduled_tick_clears_votes_without_forced_counter():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, realm, votes = _engine_with_votes(fiefs, votes={10, 11})
    engine.run_realm_tick(1, tick_slot=1)
    assert realm["day_number"] == 6
    assert realm["last_tick_slot"] == 1
    assert realm.get("forced_tick_count", 0) == 0
    assert votes == set()


def test_more_menu_shows_force_tick_button():
    kb = more_menu_kb(7, force_tick_progress=(1, 2))
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert "Тик сейчас (1/2)" in labels
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "ftv:7" in datas


def test_more_menu_hides_force_tick_without_progress():
    kb = more_menu_kb(7)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert not any(t.startswith("Тик сейчас") for t in labels)


def test_force_tick_status_line():
    fiefs = [_fief(10, 1001), _fief(11, 1002)]
    engine, _, _ = _engine_with_votes(fiefs, votes={10})
    assert engine.force_tick_status_line(1) == "Голоса за тик сейчас: 1/2"
    solo, _, _ = _engine_with_votes([_fief(10, 1001)])
    assert solo.force_tick_status_line(1) is None
