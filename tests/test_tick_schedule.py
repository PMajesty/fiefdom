"""Расписание двух тиков в день."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.tick_schedule import (
    due_tick_slot,
    format_tick_slots,
    record_slot_after_manual_tick,
)

SLOTS = [(13, 0), (19, 0)]
TZ = ZoneInfo("Europe/Moscow")


def _msk(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_format_tick_slots():
    assert format_tick_slots(SLOTS) == "13:00 и 19:00"
    assert format_tick_slots([(13, 0)]) == "13:00"


def test_due_first_slot_on_new_day():
    now = _msk(2026, 7, 16, 13, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=1,
            slots=SLOTS,
        )
        == 0
    )


def test_due_second_slot_same_day():
    now = _msk(2026, 7, 16, 19, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=0,
            slots=SLOTS,
        )
        == 1
    )


def test_not_due_before_first_slot():
    now = _msk(2026, 7, 16, 12, 59)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=1,
            slots=SLOTS,
        )
        is None
    )


def test_not_due_between_slots_after_morning():
    now = _msk(2026, 7, 16, 15, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=0,
            slots=SLOTS,
        )
        is None
    )


def test_not_due_after_both_slots():
    now = _msk(2026, 7, 16, 20, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        is None
    )


def test_catchup_evening_when_morning_missed():
    """Бот поднялся вечером - сначала утренний слот, потом вечерний."""
    now = _msk(2026, 7, 16, 20, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=1,
            slots=SLOTS,
        )
        == 0
    )
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=0,
            slots=SLOTS,
        )
        == 1
    )


def test_create_realm_blocks_same_day_with_last_slot():
    now = _msk(2026, 7, 16, 20, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        is None
    )


def test_record_slot_after_manual_tick():
    assert record_slot_after_manual_tick(
        local_now=_msk(2026, 7, 16, 10, 0), slots=SLOTS
    ) == 0
    assert record_slot_after_manual_tick(
        local_now=_msk(2026, 7, 16, 14, 0), slots=SLOTS
    ) == 0
    assert record_slot_after_manual_tick(
        local_now=_msk(2026, 7, 16, 19, 30), slots=SLOTS
    ) == 1
