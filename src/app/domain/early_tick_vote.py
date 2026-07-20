"""Правила голосования за досрочный тик континента."""
from __future__ import annotations

from datetime import date, datetime, timedelta

EARLY_TICK_DELAY = timedelta(minutes=20)
MIDPOINT_OVERRIDE_DELAY = timedelta(minutes=10)
VOTE_MIN_REMAINING = timedelta(minutes=20)


def is_active_voter(actions: int, *, actions_max: int) -> bool:
    """Активен, пока запас действий не полон."""
    return int(actions) < int(actions_max)


def quorum_needed(active_count: int) -> int:
    """Кворум: все активные, но не меньше двух голосов."""
    return max(2, int(active_count))


def votes_meet_quorum(vote_count: int, active_count: int) -> bool:
    return int(vote_count) >= quorum_needed(active_count)


def early_tick_deadline(now: datetime) -> datetime:
    return now + EARLY_TICK_DELAY


def vote_button_visible(
    *,
    next_tick_at: datetime | None,
    now: datetime,
    early_locked: bool,
) -> bool:
    """Кнопка только пока тик дальше чем на 20 минут и досрок ещё не закреплён."""
    if early_locked:
        return False
    if next_tick_at is None:
        return False
    return next_tick_at - now > VOTE_MIN_REMAINING


def effective_next_tick_at(
    scheduled: datetime | None,
    early_tick_at: datetime | None,
) -> datetime | None:
    """Ближайший тик: досрочный дедлайн, если он раньше планового."""
    if early_tick_at is None:
        return scheduled
    if scheduled is None:
        return early_tick_at
    return early_tick_at if early_tick_at <= scheduled else scheduled


def midpoint_override_on_lock(
    *,
    now: datetime,
    current_midpoint: datetime | None,
) -> datetime | None:
    """Если до середины окна больше 10 минут - сжать открытую половину до 10 минут."""
    if current_midpoint is None:
        return None
    if now >= current_midpoint:
        return None
    if current_midpoint - now <= MIDPOINT_OVERRIDE_DELAY:
        return None
    return now + MIDPOINT_OVERRIDE_DELAY


def effective_declare_midpoint(
    bounds: tuple[datetime, datetime] | None,
    override: datetime | None,
) -> datetime | None:
    if override is not None:
        return override
    if bounds is None:
        return None
    opened, closes = bounds
    return opened + (closes - opened) / 2


def wall_slot_datetime(
    *,
    slot_date: date,
    slot_index: int,
    slots: list[tuple[int, int]],
    tzinfo,
) -> datetime:
    hour, minute = slots[int(slot_index)]
    return datetime(
        slot_date.year,
        slot_date.month,
        slot_date.day,
        hour,
        minute,
        tzinfo=tzinfo,
    )


def can_consume_next_wall_slot(
    *,
    local_now: datetime,
    last_tick_local_date: date | None,
    last_tick_slot: int | None,
    slots: list[tuple[int, int]],
) -> bool:
    """Первый досрок в стенном окне съедает конец окна; лишние - нет.

    Окно открыто, когда локальные часы уже прошли wall-time последнего
    завершённого слота (или слота ещё не было).
    """
    if not slots:
        return False
    if last_tick_local_date is None or last_tick_slot is None:
        return True
    idx = int(last_tick_slot)
    if idx < 0 or idx >= len(slots):
        return True
    boundary = wall_slot_datetime(
        slot_date=last_tick_local_date,
        slot_index=idx,
        slots=slots,
        tzinfo=local_now.tzinfo,
    )
    return local_now >= boundary


def next_wall_slot_target(
    *,
    local_now: datetime,
    last_tick_local_date: date | None,
    last_tick_slot: int | None,
    slots: list[tuple[int, int]],
) -> tuple[date, int] | None:
    """Дата и индекс следующего планового слота (тот, что досрок может съесть)."""
    from app.domain.tick_schedule import due_tick_slot, next_tick_datetime

    if not slots:
        return None
    due = due_tick_slot(
        local_now=local_now,
        last_tick_local_date=last_tick_local_date,
        last_tick_slot=last_tick_slot,
        slots=slots,
    )
    if due is not None:
        return local_now.date(), int(due)
    next_at = next_tick_datetime(
        local_now=local_now,
        last_tick_local_date=last_tick_local_date,
        last_tick_slot=last_tick_slot,
        slots=slots,
    )
    if next_at is None:
        return None
    for index, (hour, minute) in enumerate(slots):
        if next_at.hour == hour and next_at.minute == minute:
            return next_at.date(), index
    return None
