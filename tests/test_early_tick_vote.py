"""Досрочный тик: активность, кворум, half-tick override, съедание слота."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import balance as B
from app.services.early_tick_vote import EarlyTickVoteService
from app.domain.early_tick_vote import (
    EARLY_TICK_DELAY,
    MIDPOINT_OVERRIDE_DELAY,
    can_consume_next_wall_slot,
    early_tick_deadline,
    effective_declare_midpoint,
    effective_next_tick_at,
    is_active_voter,
    midpoint_override_on_lock,
    next_wall_slot_target,
    quorum_needed,
    vote_button_visible,
    votes_meet_quorum,
)
from app.domain.tick_schedule import play_window_bounds, raid_declare_open, raid_lock_due

SLOTS = [(10, 0), (13, 0), (16, 0), (19, 0)]
TZ = ZoneInfo("Europe/Moscow")


def _msk(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_actions_bank_max_is_five():
    assert B.ACTIONS_BANK_MAX == 5


def test_active_when_stash_not_full():
    assert is_active_voter(4, actions_max=5)
    assert not is_active_voter(5, actions_max=5)
    assert is_active_voter(0, actions_max=5)


def test_quorum_at_least_two():
    assert quorum_needed(0) == 2
    assert quorum_needed(1) == 2
    assert quorum_needed(3) == 3
    assert votes_meet_quorum(2, 1)
    assert not votes_meet_quorum(1, 1)
    assert votes_meet_quorum(3, 3)


def test_vote_button_hidden_when_under_20_minutes():
    now = _msk(2026, 7, 20, 12, 50)
    next_at = _msk(2026, 7, 20, 13, 0)
    assert not vote_button_visible(
        next_tick_at=next_at, now=now, early_locked=False
    )
    assert vote_button_visible(
        next_tick_at=_msk(2026, 7, 20, 13, 30),
        now=now,
        early_locked=False,
    )
    assert not vote_button_visible(
        next_tick_at=_msk(2026, 7, 20, 13, 30),
        now=now,
        early_locked=True,
    )


def test_early_tick_deadline_is_20_minutes():
    now = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    assert early_tick_deadline(now) == now + EARLY_TICK_DELAY


def test_effective_next_tick_prefers_early():
    scheduled = _msk(2026, 7, 20, 13, 0)
    early = _msk(2026, 7, 20, 10, 25)
    assert effective_next_tick_at(scheduled, early) == early
    assert effective_next_tick_at(scheduled, None) == scheduled


def test_midpoint_override_compresses_to_ten_minutes():
    now = _msk(2026, 7, 20, 10, 5)
    midpoint = _msk(2026, 7, 20, 11, 30)
    override = midpoint_override_on_lock(now=now, current_midpoint=midpoint)
    assert override == now + MIDPOINT_OVERRIDE_DELAY


def test_midpoint_override_skipped_when_already_second_half():
    now = _msk(2026, 7, 20, 12, 0)
    midpoint = _msk(2026, 7, 20, 11, 30)
    assert midpoint_override_on_lock(now=now, current_midpoint=midpoint) is None


def test_midpoint_override_skipped_when_under_ten_minutes_left():
    now = _msk(2026, 7, 20, 11, 25)
    midpoint = _msk(2026, 7, 20, 11, 30)
    assert midpoint_override_on_lock(now=now, current_midpoint=midpoint) is None


def test_midpoint_override_allows_one_second_remaining():
    now = datetime(2026, 7, 20, 11, 29, 59, tzinfo=TZ)
    midpoint = _msk(2026, 7, 20, 11, 30)
    assert midpoint_override_on_lock(now=now, current_midpoint=midpoint) is None
    bounds = (_msk(2026, 7, 20, 10, 0), _msk(2026, 7, 20, 13, 0))
    assert raid_declare_open(now, bounds, midpoint=midpoint)
    assert not raid_lock_due(now, bounds, midpoint=midpoint)


def test_effective_declare_midpoint_uses_override():
    bounds = (_msk(2026, 7, 20, 10, 0), _msk(2026, 7, 20, 10, 20))
    override = _msk(2026, 7, 20, 10, 15)
    assert effective_declare_midpoint(bounds, override) == override
    geo = effective_declare_midpoint(bounds, None)
    assert geo == _msk(2026, 7, 20, 10, 10)


def test_can_consume_first_early_in_window():
    now = _msk(2026, 7, 20, 10, 5)
    assert can_consume_next_wall_slot(
        local_now=now,
        last_tick_local_date=date(2026, 7, 20),
        last_tick_slot=0,
        slots=SLOTS,
    )


def test_cannot_consume_second_early_before_wall_boundary():
    now = _msk(2026, 7, 20, 10, 40)
    assert not can_consume_next_wall_slot(
        local_now=now,
        last_tick_local_date=date(2026, 7, 20),
        last_tick_slot=1,
        slots=SLOTS,
    )


def test_can_consume_after_entering_next_wall_window():
    now = _msk(2026, 7, 20, 13, 1)
    assert can_consume_next_wall_slot(
        local_now=now,
        last_tick_local_date=date(2026, 7, 20),
        last_tick_slot=1,
        slots=SLOTS,
    )


def test_next_wall_slot_target_after_morning_tick():
    now = _msk(2026, 7, 20, 10, 5)
    assert next_wall_slot_target(
        local_now=now,
        last_tick_local_date=date(2026, 7, 20),
        last_tick_slot=0,
        slots=SLOTS,
    ) == (date(2026, 7, 20), 1)


def test_play_window_with_early_close():
    opened = _msk(2026, 7, 20, 10, 0)
    early_close = _msk(2026, 7, 20, 10, 25)
    bounds = play_window_bounds(opened, early_close)
    assert bounds == (opened, early_close)


def test_toggle_vote_locks_when_all_active_agree():
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)

    fief_a = {
        "id": 1,
        "user_id": 101,
        "realm_id": 7,
        "actions": 2,
        "frozen": False,
    }
    fief_b = {
        "id": 2,
        "user_id": 102,
        "realm_id": 7,
        "actions": 1,
        "frozen": False,
    }
    world = {
        "id": 3,
        "timezone": "Europe/Moscow",
        "early_tick_at": None,
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "play_opened_at": datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc),
        "tick_phase": "play",
    }
    local_now = _msk(2026, 7, 20, 10, 5)
    opened_local = _msk(2026, 7, 20, 10, 0)
    next_local = _msk(2026, 7, 20, 13, 0)

    engine.require_owned_fief.return_value = fief_a
    engine._world_local_now.return_value = local_now
    engine.ensure_play_opened_at.return_value = world
    engine._as_aware_utc.side_effect = lambda v: v

    db.get_realm.return_value = {"id": 7, "world_id": 3}
    db.get_world.return_value = world
    db.list_fiefs_by_world.return_value = [fief_a, fief_b]
    db.list_early_tick_votes.side_effect = [
        [102],
        [102, 101],
        [102, 101],
    ]
    db.transaction.return_value.__enter__ = MagicMock(return_value=None)
    db.transaction.return_value.__exit__ = MagicMock(return_value=False)
    service.scheduled_next_tick_local = MagicMock(return_value=next_local)

    result = service.toggle_vote(1, 101)
    assert result.locked is True
    assert result.notify_user_ids == (101, 102)
    assert result.early_tick_at is not None
    db.update_world.assert_called()
    kwargs = db.update_world.call_args.kwargs
    assert "early_tick_at" in kwargs
    assert "declare_midpoint_at" in kwargs
    # 10:05 → mid 11:30, >10м → compress to lock+10м
    assert kwargs["declare_midpoint_at"] == (
        local_now + MIDPOINT_OVERRIDE_DELAY
    ).astimezone(timezone.utc)


def test_home_kb_shows_early_tick_row():
    from app.ui.keyboards.hubs import home_kb

    kb = home_kb(9, "Собрать", "st:9", early_tick_label="Тик раньше (1/2)")
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "etv:9" in flat


def test_reconcile_locks_when_active_set_shrinks():
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)
    fief_a = {
        "id": 1,
        "user_id": 101,
        "realm_id": 7,
        "actions": 2,
        "frozen": False,
    }
    fief_b = {
        "id": 2,
        "user_id": 102,
        "realm_id": 7,
        "actions": 1,
        "frozen": False,
    }
    # C уже не активен (полный запас), но раньше входил в кворум из трёх.
    world = {
        "id": 3,
        "timezone": "Europe/Moscow",
        "early_tick_at": None,
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "play_opened_at": datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc),
        "tick_phase": "play",
    }
    local_now = _msk(2026, 7, 20, 10, 5)
    next_local = _msk(2026, 7, 20, 13, 0)

    def _update_world(_wid, **fields):
        world.update(fields)

    db.get_world.side_effect = lambda _wid=None: dict(world)
    db.list_fiefs_by_world.return_value = [fief_a, fief_b]
    db.list_early_tick_votes.return_value = [101, 102]
    db.transaction.return_value.__enter__ = MagicMock(return_value=None)
    db.transaction.return_value.__exit__ = MagicMock(return_value=False)
    db.update_world.side_effect = _update_world
    engine._world_local_now.return_value = local_now
    engine.ensure_play_opened_at.side_effect = lambda _wid: dict(world)
    service.scheduled_next_tick_local = MagicMock(return_value=next_local)

    result = service.reconcile_quorum(3)
    assert result is not None
    assert result.locked is True
    assert result.notify_user_ids == (101, 102)
    assert world.get("early_tick_at") is not None


def test_maybe_lock_refuses_when_under_20_minutes():
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)
    world = {
        "id": 3,
        "timezone": "Europe/Moscow",
        "early_tick_at": None,
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
    }
    db.get_world.return_value = world
    db.list_fiefs_by_world.return_value = [
        {"id": 1, "user_id": 101, "actions": 1, "frozen": False},
        {"id": 2, "user_id": 102, "actions": 1, "frozen": False},
    ]
    db.list_early_tick_votes.return_value = [101, 102]
    engine._world_local_now.return_value = _msk(2026, 7, 20, 12, 50)
    service.scheduled_next_tick_local = MagicMock(
        return_value=_msk(2026, 7, 20, 13, 0)
    )
    result = service._maybe_lock(3)
    assert result.locked is False
    db.update_world.assert_not_called()


def test_arm_and_pending_early_tick_slot():
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)
    service.arm_early_tick_fire(9, 2)
    db.update_world.assert_called_with(9, early_tick_pending_slot=2)
    assert service.pending_early_tick_slot({"early_tick_pending_slot": 2}) == 2
    assert service.pending_early_tick_slot({}) is None


def test_clear_vote_state_resets_markers_and_votes():
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)
    db.transaction.return_value.__enter__ = MagicMock(return_value=None)
    db.transaction.return_value.__exit__ = MagicMock(return_value=False)
    service.clear_vote_state(4)
    db.clear_early_tick_votes.assert_called_with(4)
    db.update_world.assert_called_with(
        4,
        early_tick_at=None,
        declare_midpoint_at=None,
        early_tick_pending_slot=None,
    )


@pytest.mark.asyncio
async def test_scheduler_suppresses_wall_slot_while_early_locked():
    from unittest.mock import AsyncMock, patch

    from app.scheduler import _scheduler_tick

    world = {
        "id": 1,
        "timezone": "Europe/Moscow",
        "early_tick_at": datetime(2026, 7, 20, 10, 25, tzinfo=timezone.utc),
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "tick_phase": "play",
    }
    engine = MagicMock()
    engine.default_world.return_value = world
    engine.world.return_value = world
    engine.realms_of_world.return_value = []
    engine.world_tick_incomplete.return_value = False
    engine.early_tick_due.return_value = False
    engine.reconcile_early_tick_quorum.return_value = None
    engine.maybe_lock_raids_at_midpoint.return_value = 0
    engine.maybe_due_rumors.return_value = []
    engine.run_world_tick.return_value = {"realms": [], "raid_notices": []}

    with (
        patch("app.scheduler.get_engine", return_value=engine),
        patch("app.scheduler.due_tick_slot", return_value=1) as due,
        patch("app.scheduler._maybe_post_world_catastrophe", new=AsyncMock()),
        patch("app.scheduler.announce_pending_patches", new=AsyncMock()),
    ):
        await _scheduler_tick(MagicMock())

    due.assert_not_called()
    engine.run_world_tick.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_early_due_arms_and_fires_slot():
    from unittest.mock import AsyncMock, patch

    from app.scheduler import _scheduler_tick

    world = {
        "id": 1,
        "timezone": "Europe/Moscow",
        "early_tick_at": datetime(2026, 7, 20, 7, 20, tzinfo=timezone.utc),
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "tick_phase": "play",
    }
    engine = MagicMock()
    engine.default_world.return_value = world
    engine.world.return_value = world
    engine.realms_of_world.return_value = []
    engine.world_tick_incomplete.return_value = False
    engine.early_tick_due.return_value = True
    engine.tick_slot_for_early_fire.return_value = 1
    engine.run_world_tick.return_value = {
        "realms": [],
        "raid_notices": [],
        "resumed": False,
        "incomplete": False,
    }

    with (
        patch("app.scheduler.get_engine", return_value=engine),
        patch("app.scheduler._maybe_post_world_catastrophe", new=AsyncMock()),
        patch("app.scheduler.announce_pending_patches", new=AsyncMock()),
    ):
        await _scheduler_tick(MagicMock())

    engine.arm_early_tick_fire.assert_called_once_with(1, 1)
    engine.run_world_tick.assert_called_once_with(1, tick_slot=1)


@pytest.mark.asyncio
async def test_scheduler_reconcile_notifies_on_lock():
    from unittest.mock import AsyncMock, patch

    from app.scheduler import _scheduler_tick
    from app.services.early_tick_vote import EarlyTickVoteResult

    world = {
        "id": 1,
        "timezone": "Europe/Moscow",
        "early_tick_at": None,
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "tick_phase": "play",
    }
    early_at = datetime(2026, 7, 20, 7, 25, tzinfo=timezone.utc)
    engine = MagicMock()
    engine.default_world.return_value = world
    engine.world.return_value = world
    engine.realms_of_world.return_value = []
    engine.world_tick_incomplete.return_value = False
    engine.early_tick_due.return_value = False
    engine.maybe_lock_raids_at_midpoint.return_value = 0
    engine.maybe_due_rumors.return_value = []
    engine.reconcile_early_tick_quorum.return_value = EarlyTickVoteResult(
        alert="ok",
        locked=True,
        notify_user_ids=(11, 22),
        early_tick_at=early_at,
    )
    engine.early_tick_lock_announcement.return_value = "locked"
    engine.run_world_tick.return_value = {"realms": [], "raid_notices": []}

    with (
        patch("app.scheduler.get_engine", return_value=engine),
        patch("app.scheduler.due_tick_slot", return_value=None),
        patch("app.scheduler.send_game", new=AsyncMock()) as send,
        patch("app.scheduler._maybe_post_world_catastrophe", new=AsyncMock()),
        patch("app.scheduler.announce_pending_patches", new=AsyncMock()),
    ):
        await _scheduler_tick(MagicMock())

    assert send.await_count == 2
    notified = sorted(call.args[1] for call in send.await_args_list)
    assert notified == [11, 22]


def test_lock_without_compress_keeps_original_midpoint():
    """Если до середины ≤10м - закрепляем исходную mid, не геометрию укороченного окна."""
    engine = MagicMock()
    db = MagicMock()
    service = EarlyTickVoteService(engine, db)

    fief_a = {
        "id": 1,
        "user_id": 101,
        "realm_id": 7,
        "actions": 2,
        "frozen": False,
    }
    fief_b = {
        "id": 2,
        "user_id": 102,
        "realm_id": 7,
        "actions": 1,
        "frozen": False,
    }
    world = {
        "id": 3,
        "timezone": "Europe/Moscow",
        "early_tick_at": None,
        "last_tick_local_date": date(2026, 7, 20),
        "last_tick_slot": 0,
        "play_opened_at": datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc),
        "tick_phase": "play",
    }
    # 11:22, mid 11:30 → ≤10м, override compress не срабатывает.
    local_now = _msk(2026, 7, 20, 11, 22)
    opened_local = _msk(2026, 7, 20, 10, 0)
    next_local = _msk(2026, 7, 20, 13, 0)
    original_mid = _msk(2026, 7, 20, 11, 30)

    engine.require_owned_fief.return_value = fief_a
    engine._world_local_now.return_value = local_now
    engine.ensure_play_opened_at.return_value = world
    engine._as_aware_utc.side_effect = lambda v: v

    db.get_realm.return_value = {"id": 7, "world_id": 3}
    db.get_world.return_value = world
    db.list_fiefs_by_world.return_value = [fief_a, fief_b]
    db.list_early_tick_votes.side_effect = [[102], [102, 101]]
    db.transaction.return_value.__enter__ = MagicMock(return_value=None)
    db.transaction.return_value.__exit__ = MagicMock(return_value=False)

    # scheduled bounds via real next_tick + mocked opened
    service.scheduled_next_tick_local = MagicMock(return_value=next_local)
    service._as_aware_utc = MagicMock(
        return_value=datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)
    )

    result = service.toggle_vote(1, 101)
    assert result.locked is True
    kwargs = db.update_world.call_args.kwargs
    assert kwargs["declare_midpoint_at"] == original_mid.astimezone(timezone.utc)

    # После lock: укороченное окно не должно закрыть declare до 11:30.
    from app.domain.early_tick_vote import effective_declare_midpoint
    from app.domain.tick_schedule import raid_declare_open

    early_close = local_now + EARLY_TICK_DELAY
    short_bounds = (opened_local, early_close)
    pinned = kwargs["declare_midpoint_at"].astimezone(TZ)
    assert effective_declare_midpoint(short_bounds, pinned) == original_mid
    assert raid_declare_open(local_now, short_bounds, midpoint=pinned)
    assert raid_declare_open(
        datetime(2026, 7, 20, 11, 29, 59, tzinfo=TZ),
        short_bounds,
        midpoint=pinned,
    )
