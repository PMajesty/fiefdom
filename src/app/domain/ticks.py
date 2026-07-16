"""Хелперы тикового времени долины."""
from __future__ import annotations


def tick_active(until_tick: int | None, tick_index: int) -> bool:
    """Эффект с until_tick активен, пока текущий tick_index строго меньше until."""
    if until_tick is None:
        return False
    return int(until_tick) > int(tick_index)


def ticks_until(until_tick: int | None, tick_index: int) -> int:
    if until_tick is None:
        return 0
    return max(0, int(until_tick) - int(tick_index))
