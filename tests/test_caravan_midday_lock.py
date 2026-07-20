"""Part B: обозы делят окно declare/cancel с набегами и лочатся на midday."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.guide import game_guide
from app.engine import Engine
from tests.test_caravans import _caravan_stateful_engine
from tests.test_raid_night_characterization import (
    _base_fief,
    _inject_raid_intent,
    _raid_night_engine,
)


def test_declare_caravan_refused_after_midpoint():
    engine, fiefs, _intents = _caravan_stateful_engine()
    engine.raid_declare_is_open = MagicMock(return_value=False)
    with pytest.raises(ValueError, match="Поздно объявлять обоз"):
        engine.declare_caravan(1, 2, B.RES_GRAIN, 5)
    assert fiefs[1]["grain"] == 50


def test_cancel_caravan_refused_when_locked():
    engine, fiefs, intents = _caravan_stateful_engine()
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    intents[0]["status"] = "locked"
    with pytest.raises(ValueError, match="закрытия заявок"):
        engine.cancel_caravan_intent(1, result.intent_id)
    assert fiefs[1]["grain"] == 40
    assert intents[0]["status"] == "locked"


def test_declare_caravan_dm_mentions_lock_deadline():
    engine, _fiefs, _intents = _caravan_stateful_engine()
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 8)
    assert "Вернуть можно до 17.07 12:00" in result.dm_text
    assert "17.07 18:00" in result.dm_text


def test_lock_open_travel_intents_counts_raids_caravans_and_cover():
    db = MagicMock()
    engine = Engine(db)
    db.get_world.return_value = {"id": 3, "tick_index": 7}
    db.list_raid_intents.return_value = []
    db.lock_action_intents.side_effect = lambda wid, tick, *, kind: {
        "raid": 2,
        "caravan": 3,
        "cover_stance": 1,
    }[kind]
    assert engine.lock_open_travel_intents(3) == 6
    assert db.lock_action_intents.call_count == 3
    kinds = [c.kwargs["kind"] for c in db.lock_action_intents.call_args_list]
    assert kinds == ["raid", "caravan", "cover_stance"]


def test_maybe_lock_at_midpoint_locks_caravans_too():
    db = MagicMock()
    engine = Engine(db)
    world = {
        "id": 1,
        "timezone": "UTC",
        "tick_phase": "play",
        "play_opened_at": datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
        "tick_index": 4,
    }
    db.get_world.return_value = world
    engine.ensure_play_opened_at = MagicMock(return_value=world)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine.play_window_bounds_for_world = MagicMock(
        return_value=(
            datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 17, 14, 0, tzinfo=timezone.utc),
        )
    )
    with patch.object(
        engine,
        "_world_local_now",
        return_value=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
    ):
        with patch.object(engine, "lock_open_travel_intents", return_value=4) as lock:
            n = engine.maybe_lock_raids_at_midpoint(1)
    assert n == 4
    lock.assert_called_once_with(1)


def test_night_raid_loots_locked_caravan_escrow():
    atk = _base_fief(
        1, realm_id=1, user_id=101, name="Атакующий", might=5, grain=0, goods=0
    )
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=0,
        grain=0,
        goods=0,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        watch_defense=1.0,
        tick_index=10,
    )
    engine.barn_level = MagicMock(return_value=0)
    engine.db.create_action_intent(
        world_id=1,
        tick_index=10,
        fief_id=2,
        kind="caravan",
        status="locked",
        payload={
            "receiver_id": 99,
            "res": B.RES_GRAIN,
            "amt": 50,
            "escrowed": True,
            "sender_realm_id": 1,
            "receiver_realm_id": 1,
            "is_public": False,
        },
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=60, tick_index=10
    )
    fixed = MagicMock()
    fixed.success = True
    fixed.ratio = 2.0
    fixed.might_lost = 0
    fixed.stolen = {B.RES_GRAIN: 50, B.RES_GOODS: 0}
    fixed.defense_used = 1
    fixed.intercept_applied = False
    fixed.public_line = "Атакующий ограбил Жертва"
    with patch("app.services.night_raids.resolve_raid", return_value=fixed):
        report = engine.resolve_pending_raids(1, 10)
    assert report.resolved_count == 1
    assert engine._fiefs[1]["grain"] == 50
    caravan = engine._intents[0]
    assert caravan["status"] == "cancelled"
    assert caravan["payload"]["amt"] == 0


def test_locked_caravan_still_delivers_at_resolve():
    engine, fiefs, intents = _caravan_stateful_engine(grain_to=5)
    engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    intents[0]["status"] = "locked"
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 1
    assert fiefs[2]["grain"] == 15
    assert fiefs[1]["grain"] == 40
    assert intents[0]["status"] == "resolved"
    assert any("дошёл" in n.text.lower() for n in report.notices if n.kind == "dm")


def test_close_play_force_locks_travel_intents():
    from app.services.world_tick import WorldTickOrchestrator

    db = MagicMock()
    engine = Engine(db)
    world = {"id": 1, "tick_index": 4, "tick_phase": "play"}
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine.lock_open_travel_intents = MagicMock(return_value=3)
    night_notice = MagicMock(text="ночь")
    night = MagicMock(notices=[night_notice])
    engine.resolve_pending_raids = MagicMock(return_value=night)
    engine.resolve_remaining_cover_stances = MagicMock(return_value=[])
    engine.resolve_pending_caravans = MagicMock(
        return_value=MagicMock(notices=[], digest_lines=[])
    )
    orch = WorldTickOrchestrator(engine, db)
    report = orch._close_play_day_raids(1, 4, world)
    engine.lock_open_travel_intents.assert_called_once_with(1)
    engine.resolve_pending_raids.assert_called_once_with(1, 4)
    engine.resolve_remaining_cover_stances.assert_called_once_with(1, 4)
    engine.resolve_pending_caravans.assert_called_once_with(1, 4)
    assert report.notices[0] is night_notice


def test_guide_notes_shared_declare_window():
    text = game_guide()
    assert "первой половине окна тика, что и набег" in text
    assert "вернуть воз кнопкой можно до середины окна" in text
