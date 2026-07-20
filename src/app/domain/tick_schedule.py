"""Расписание тиков долины по локальному времени."""
from __future__ import annotations

from datetime import date, datetime, timedelta

# Прежний layout (до 4 тиков/день): индексы 0/1 = 13:00/19:00.
LEGACY_TWO_TICK_SLOTS: list[tuple[int, int]] = [(13, 0), (19, 0)]


def validate_tick_slots(slots: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Слоты должны быть непустыми, уникальными и строго по возрастанию времени."""
    if not slots:
        raise ValueError("tick_slots: список слотов пуст")
    prev: tuple[int, int] | None = None
    seen: set[tuple[int, int]] = set()
    for hour, minute in slots:
        h, m = int(hour), int(minute)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"tick_slots: недопустимое время {h:02d}:{m:02d}")
        key = (h, m)
        if key in seen:
            raise ValueError(f"tick_slots: дубликат слота {h:02d}:{m:02d}")
        if prev is not None and key <= prev:
            raise ValueError(
                "tick_slots: слоты должны идти строго по возрастанию времени"
            )
        seen.add(key)
        prev = key
    return [(int(h), int(m)) for h, m in slots]


def remap_last_tick_slot(
    last_slot: int | None,
    *,
    from_slots: list[tuple[int, int]],
    to_slots: list[tuple[int, int]],
) -> int | None:
    """Перенос last_tick_slot при смене layout: по wall-clock времени слота."""
    if last_slot is None:
        return None
    if not from_slots or not to_slots:
        return int(last_slot)
    idx = int(last_slot)
    if idx < 0:
        return None
    if idx >= len(from_slots):
        return min(idx, len(to_slots) - 1)
    hour, minute = from_slots[idx]
    for new_idx, (h, m) in enumerate(to_slots):
        if h == hour and m == minute:
            return new_idx
    best = -1
    for new_idx, (h, m) in enumerate(to_slots):
        if (h, m) <= (hour, minute):
            best = new_idx
    return best if best >= 0 else None


def format_tick_slots(slots: list[tuple[int, int]]) -> str:
    """Человекочитаемые слоты: '13:00 и 19:00' или '10:00, 13:00, 16:00 и 19:00'."""
    parts = [f"{h:02d}:{m:02d}" for h, m in slots]
    if not parts:
        return "-"
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} и {parts[1]}"
    return ", ".join(parts[:-1]) + f" и {parts[-1]}"


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


def schedule_anchor_at(
    *,
    local_now: datetime,
    slots: list[tuple[int, int]],
) -> tuple[date | None, int | None]:
    """Якорь планового расписания: только слоты, время которых уже наступило.

    Будущие слоты того же дня остаются открытыми. Если ни один слот ещё не
    наступил - (None, None). Нужен при основании континента, чтобы не сжечь
    вечерний тик из-за утреннего/дневного старта.
    """
    if not slots:
        return None, None
    last_passed: int | None = None
    for index, (hour, minute) in enumerate(slots):
        if slot_time_reached(local_now, hour, minute):
            last_passed = index
    if last_passed is None:
        return None, None
    return local_now.date(), last_passed


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


def play_window_bounds(
    play_opened_at: datetime | None,
    next_tick_at: datetime | None,
) -> tuple[datetime, datetime] | None:
    """Окно play: от открытия до следующего слота. None если часов нет."""
    if play_opened_at is None or next_tick_at is None:
        return None
    if next_tick_at <= play_opened_at:
        return None
    return (play_opened_at, next_tick_at)


def raid_declare_midpoint(bounds: tuple[datetime, datetime]) -> datetime:
    opened, closes = bounds
    return opened + (closes - opened) / 2


def raid_declare_open(
    now: datetime,
    bounds: tuple[datetime, datetime] | None,
    *,
    midpoint: datetime | None = None,
) -> bool:
    """True до середины окна play. Без границ - закрыто (осторожный дефолт)."""
    if bounds is None:
        return False
    point = midpoint if midpoint is not None else raid_declare_midpoint(bounds)
    return now < point


def raid_lock_due(
    now: datetime,
    bounds: tuple[datetime, datetime] | None,
    *,
    midpoint: datetime | None = None,
) -> bool:
    """True после середины окна play."""
    if bounds is None:
        return False
    point = midpoint if midpoint is not None else raid_declare_midpoint(bounds)
    return now >= point
