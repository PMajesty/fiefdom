"""Расписание тиков долины по локальному времени."""
from __future__ import annotations

from datetime import date, datetime, timedelta


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


def _slot_datetime(local_now: datetime, day: date, hour: int, minute: int) -> datetime:
    return datetime(
        day.year,
        day.month,
        day.day,
        hour,
        minute,
        tzinfo=local_now.tzinfo,
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


def next_tick_datetime(
    *,
    local_now: datetime,
    last_tick_local_date: date | None,
    last_tick_slot: int | None,
    slots: list[tuple[int, int]],
) -> datetime | None:
    """Когда ожидается следующий тик в локальном времени долины.

    Если слот уже просрочен (бот ещё не успел), возвращает время того слота
    (не позже local_now) - UI показывает "сейчас".
    """
    if not slots:
        return None

    due_index = due_tick_slot(
        local_now=local_now,
        last_tick_local_date=last_tick_local_date,
        last_tick_slot=last_tick_slot,
        slots=slots,
    )
    if due_index is not None:
        hour, minute = slots[due_index]
        return _slot_datetime(local_now, local_now.date(), hour, minute)

    local_date = local_now.date()
    last_slot = -1 if last_tick_slot is None else int(last_tick_slot)
    for index, (hour, minute) in enumerate(slots):
        slot_dt = _slot_datetime(local_now, local_date, hour, minute)
        if slot_dt <= local_now:
            continue
        if last_tick_local_date is None:
            return slot_dt
        if local_date > last_tick_local_date:
            return slot_dt
        if local_date == last_tick_local_date and index > last_slot:
            return slot_dt
    tomorrow = local_date + timedelta(days=1)
    hour, minute = slots[0]
    return _slot_datetime(local_now, tomorrow, hour, minute)


def format_next_tick_line(
    next_at: datetime | None,
    *,
    local_now: datetime,
) -> str:
    """Строка для карточки статуса."""
    if next_at is None:
        return "Следующий тик: -"
    if next_at <= local_now:
        return "Следующий тик: сейчас"
    return f"Следующий тик: {next_at.strftime('%d.%m %H:%M')}"
