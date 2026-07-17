"""Календарь континента: день/тик и опциональный сезон.

1 tick = 1 игровой день - намеренно. day_number двигает Engine по своим правилам;
этот модуль не меняет часы, только читает/вычисляет season-метаданные.
Пока season_key IS NULL - сезонов нет (live no-op).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SeasonState:
    """Активный сезон мира, если задан в storage."""

    key: str
    tick_start: int
    length_ticks: int | None = None

    def ticks_elapsed(self, tick_index: int) -> int:
        return max(0, int(tick_index) - int(self.tick_start))

    def ticks_remaining(self, tick_index: int) -> int | None:
        if self.length_ticks is None:
            return None
        return int(self.length_ticks) - self.ticks_elapsed(tick_index)


def season_from_world(world: dict[str, Any] | None) -> SeasonState | None:
    """None если сезон не сконфигурирован (текущий live-мир)."""
    if not world:
        return None
    key = world.get("season_key")
    if key is None or key == "":
        return None
    start_raw = world.get("season_tick_start")
    tick_start = 0 if start_raw is None else int(start_raw)
    length_raw = world.get("season_length_ticks")
    length = None if length_raw is None else int(length_raw)
    return SeasonState(key=str(key), tick_start=tick_start, length_ticks=length)


def season_fields(
    *,
    key: str | None,
    tick_start: int | None = None,
    length_ticks: int | None = None,
) -> dict[str, Any]:
    """Поля для update_world. key=None сбрасывает сезон."""
    if key is None or key == "":
        return {
            "season_key": None,
            "season_tick_start": None,
            "season_length_ticks": None,
        }
    return {
        "season_key": str(key),
        "season_tick_start": 0 if tick_start is None else int(tick_start),
        "season_length_ticks": None if length_ticks is None else int(length_ticks),
    }


def game_day_from_tick(tick_index: int) -> int:
    """Игровой день = tick_index при модели 1 tick = 1 day (индекс с 0 → день 0+)."""
    return int(tick_index)
