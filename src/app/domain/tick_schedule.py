"""Расписание тиков долины по локальному времени."""
from __future__ import annotations

from datetime import date, datetime


def format_tick_slots(slots: list[tuple[int, int]]) -> str:
    """Человекочитаемые слоты: '13:00 и 19:00'."""
    parts = [f"{h:02d}:{m:02d}" for h, m in slots]
    if not parts:
        return "-"
    if len(parts) == 1:
        return parts[0]
    return " и ".join(parts)


def slot_time_reached(local_now: datetime, hour: int, minute: int) -> bool:
    return local_now.hour > hour or (
        local_now.hour == hour and local_now.minute >= minute
    )


def due_tick_slot(
    *,
    local_now: datetime,
    last_tick_local_date: date | None,
    last_tick_slot: int | None,
    slots: list[tuple[int, int]],
) -> int | None:
    """Индекс следующего просроченного слота или None."""
    if not slots:
        return None
    local_date = local_now.date()
    last_slot = -1 if last_tick_slot is None else int(last_tick_slot)

    for index, (hour, minute) in enumerate(slots):
        if not slot_time_reached(local_now, hour, minute):
            continue
        if last_tick_local_date is None:
            return index
        if local_date > last_tick_local_date:
            return index
        if local_date == last_tick_local_date and index > last_slot:
            return index
    return None


def record_slot_after_manual_tick(
    *,
    local_now: datetime,
    slots: list[tuple[int, int]],
) -> int:
    """Какой слот записать после ручного тика (админ)."""
    if not slots:
        return 0
    best = 0
    for index, (hour, minute) in enumerate(slots):
        if slot_time_reached(local_now, hour, minute):
            best = index
    return best
