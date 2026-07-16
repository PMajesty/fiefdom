"""Расписание четырёх тиков в день."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import tick_slots
from app.domain.tick_schedule import (
    LEGACY_TWO_TICK_SLOTS,
    due_tick_slot,
    format_next_tick_line,
    format_tick_slots,
    next_tick_datetime,
    remap_last_tick_slot,
    schedule_anchor_at,
    validate_tick_slots,
)

SLOTS = [(10, 0), (13, 0), (16, 0), (19, 0)]
TZ = ZoneInfo("Europe/Moscow")


def _msk(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_format_tick_slots():
    assert format_tick_slots(SLOTS) == "10:00, 13:00, 16:00 и 19:00"
    assert format_tick_slots([(13, 0), (19, 0)]) == "13:00 и 19:00"
    assert format_tick_slots([(13, 0)]) == "13:00"
    assert format_tick_slots([]) == "-"


def test_config_tick_slots_defaults_four_per_day():
    assert tick_slots() == SLOTS


def test_validate_tick_slots_rejects_duplicates_and_disorder():
    with pytest.raises(ValueError, match="дубликат"):
        validate_tick_slots([(13, 0), (16, 0), (19, 0), (19, 0)])
    with pytest.raises(ValueError, match="возрастанию"):
        validate_tick_slots([(13, 0), (19, 0), (16, 0)])
    with pytest.raises(ValueError, match="пуст"):
        validate_tick_slots([])


def test_remap_last_tick_slot_from_legacy_two_to_four():
    assert (
        remap_last_tick_slot(
            0, from_slots=LEGACY_TWO_TICK_SLOTS, to_slots=SLOTS
        )
        == 1
    )
    assert (
        remap_last_tick_slot(
            1, from_slots=LEGACY_TWO_TICK_SLOTS, to_slots=SLOTS
        )
        == 3
    )
    assert (
        remap_last_tick_slot(
            None, from_slots=LEGACY_TWO_TICK_SLOTS, to_slots=SLOTS
        )
        is None
    )


def test_due_first_slot_on_new_day():
    now = _msk(2026, 7, 16, 10, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=3,
            slots=SLOTS,
        )
        == 0
    )


def test_due_second_slot_same_day():
    now = _msk(2026, 7, 16, 13, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=0,
            slots=SLOTS,
        )
        == 1
    )


def test_due_third_and_fourth_slots_same_day():
    assert (
        due_tick_slot(
            local_now=_msk(2026, 7, 16, 16, 0),
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        == 2
    )
    assert (
        due_tick_slot(
            local_now=_msk(2026, 7, 16, 19, 0),
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=2,
            slots=SLOTS,
        )
        == 3
    )


def test_not_due_before_first_slot():
    now = _msk(2026, 7, 16, 9, 59)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=3,
            slots=SLOTS,
        )
        is None
    )


def test_not_due_between_slots_after_morning():
    now = _msk(2026, 7, 16, 14, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        is None
    )


def test_not_due_after_all_slots():
    now = _msk(2026, 7, 16, 20, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=3,
            slots=SLOTS,
        )
        is None
    )


def test_catchup_runs_slots_in_order_when_day_missed():
    """Бот поднялся вечером - сначала утренний слот, потом следующие по порядку."""
    now = _msk(2026, 7, 16, 20, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 15),
            last_tick_slot=3,
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
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        == 2
    )
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=2,
            slots=SLOTS,
        )
        == 3
    )


def test_schedule_anchor_before_first_slot():
    assert schedule_anchor_at(local_now=_msk(2026, 7, 16, 9, 0), slots=SLOTS) == (
        None,
        None,
    )


def test_schedule_anchor_between_slots_keeps_later():
    """Основание днём закрывает только прошедшие слоты."""
    assert schedule_anchor_at(local_now=_msk(2026, 7, 16, 14, 0), slots=SLOTS) == (
        date(2026, 7, 16),
        1,
    )
    assert (
        due_tick_slot(
            local_now=_msk(2026, 7, 16, 16, 0),
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=1,
            slots=SLOTS,
        )
        == 2
    )


def test_schedule_anchor_after_evening_closes_day():
    assert schedule_anchor_at(local_now=_msk(2026, 7, 16, 20, 0), slots=SLOTS) == (
        date(2026, 7, 16),
        3,
    )
    assert (
        due_tick_slot(
            local_now=_msk(2026, 7, 16, 20, 0),
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=3,
            slots=SLOTS,
        )
        is None
    )


def test_null_last_slot_means_no_scheduled_slot_done():
    """NULL last_tick_slot - первый слот ещё не закрыт расписанием."""
    now = _msk(2026, 7, 16, 10, 0)
    assert (
        due_tick_slot(
            local_now=now,
            last_tick_local_date=date(2026, 7, 16),
            last_tick_slot=None,
            slots=SLOTS,
        )
        == 0
    )


def test_next_tick_before_morning():
    now = _msk(2026, 7, 16, 9, 0)
    assert next_tick_datetime(
        local_now=now,
        last_tick_local_date=date(2026, 7, 15),
        last_tick_slot=3,
        slots=SLOTS,
    ) == _msk(2026, 7, 16, 10, 0)


def test_next_tick_between_slots_after_second():
    now = _msk(2026, 7, 16, 14, 0)
    assert next_tick_datetime(
        local_now=now,
        last_tick_local_date=date(2026, 7, 16),
        last_tick_slot=1,
        slots=SLOTS,
    ) == _msk(2026, 7, 16, 16, 0)


def test_next_tick_after_all_is_tomorrow_morning():
    now = _msk(2026, 7, 16, 20, 0)
    assert next_tick_datetime(
        local_now=now,
        last_tick_local_date=date(2026, 7, 16),
        last_tick_slot=3,
        slots=SLOTS,
    ) == _msk(2026, 7, 17, 10, 0)


def test_next_tick_due_returns_overdue_slot():
    """Просроченный утренний слот - следующий тик уже "сейчас"."""
    now = _msk(2026, 7, 16, 20, 0)
    assert next_tick_datetime(
        local_now=now,
        last_tick_local_date=date(2026, 7, 15),
        last_tick_slot=3,
        slots=SLOTS,
    ) == _msk(2026, 7, 16, 10, 0)


def test_format_next_tick_line():
    now = _msk(2026, 7, 16, 14, 0)
    assert (
        format_next_tick_line(_msk(2026, 7, 16, 16, 0), local_now=now)
        == "Следующий тик: 16.07 16:00"
    )
    assert (
        format_next_tick_line(_msk(2026, 7, 16, 13, 0), local_now=now)
        == "Следующий тик: сейчас"
    )
    assert format_next_tick_line(None, local_now=now) == "Следующий тик: -"
