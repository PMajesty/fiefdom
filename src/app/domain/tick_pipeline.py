"""Фазы мирового тика и окно действий игрока."""
from __future__ import annotations

TICK_PHASE_PLAY = "play"
TICK_PHASE_ECONOMY = "economy"

_PLAY_BLOCKED = (
    "Континент ещё догоняет тик. "
    "Действия временно недоступны."
)


def normalize_tick_phase(raw: str | None) -> str:
    if raw == TICK_PHASE_ECONOMY:
        return TICK_PHASE_ECONOMY
    return TICK_PHASE_PLAY


def needs_economy_wake(world: dict) -> bool:
    """Scheduler: добить play после crash, не открывая новый слот."""
    return normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY


class ActionWindow:
    """Игровые мутации только в play при полностью догнанном тике."""

    BLOCKED_MESSAGE = _PLAY_BLOCKED

    @staticmethod
    def allows(*, tick_phase: str | None, incomplete: bool) -> bool:
        return normalize_tick_phase(tick_phase) == TICK_PHASE_PLAY and not incomplete

    @classmethod
    def require(cls, *, tick_phase: str | None, incomplete: bool) -> None:
        if not cls.allows(tick_phase=tick_phase, incomplete=incomplete):
            raise ValueError(cls.BLOCKED_MESSAGE)


class TickPipeline:
    """Минимальный конвейер: economy на fan-out, play после догона всех долин."""

    @staticmethod
    def economy_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_ECONOMY}

    @staticmethod
    def play_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_PLAY}
