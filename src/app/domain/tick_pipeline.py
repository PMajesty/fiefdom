"""Фазы мирового тика, capabilities окна действий, конвейер переходов.

Live: краткий resolve (ночные набеги) → economy → play.
Фаза orders остаётся substrate и live-кодом не активируется.
"""
from __future__ import annotations

from dataclasses import dataclass

TICK_PHASE_PLAY = "play"
TICK_PHASE_ECONOMY = "economy"
# Substrate: live-переходы сюда не входят.
TICK_PHASE_ORDERS = "orders"
TICK_PHASE_RESOLVE = "resolve"

LIVE_TICK_PHASES: frozenset[str] = frozenset(
    {TICK_PHASE_PLAY, TICK_PHASE_ECONOMY, TICK_PHASE_RESOLVE}
)
ALL_TICK_PHASES: frozenset[str] = frozenset(
    {
        TICK_PHASE_PLAY,
        TICK_PHASE_ECONOMY,
        TICK_PHASE_ORDERS,
        TICK_PHASE_RESOLVE,
    }
)

_PLAY_BLOCKED = (
    "Континент ещё догоняет тик. "
    "Действия временно недоступны."
)


@dataclass(frozen=True)
class PhaseCapabilities:
    """Что разрешено в фазе (независимые флаги)."""

    allow_mutations: bool = False
    allow_orders: bool = False
    allow_economy: bool = False


_PHASE_CAPABILITIES: dict[str, PhaseCapabilities] = {
    TICK_PHASE_ECONOMY: PhaseCapabilities(allow_economy=True),
    TICK_PHASE_PLAY: PhaseCapabilities(allow_mutations=True),
    TICK_PHASE_ORDERS: PhaseCapabilities(allow_orders=True),
    TICK_PHASE_RESOLVE: PhaseCapabilities(),
}


def normalize_tick_phase(raw: str | None) -> str:
    """Live-нормализация: economy остаётся; неизвестное и NULL → play.

    Будущие фазы (orders/resolve) сохраняются как есть, если попадут в storage,
    чтобы capabilities не маскировались под play.
    """
    if raw == TICK_PHASE_ECONOMY:
        return TICK_PHASE_ECONOMY
    if raw in (TICK_PHASE_ORDERS, TICK_PHASE_RESOLVE):
        return str(raw)
    return TICK_PHASE_PLAY


def phase_capabilities(tick_phase: str | None) -> PhaseCapabilities:
    phase = normalize_tick_phase(tick_phase)
    return _PHASE_CAPABILITIES.get(phase, PhaseCapabilities())


def needs_economy_wake(world: dict) -> bool:
    """Scheduler: добить play после crash, не открывая новый слот."""
    return normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY


def needs_resolve_wake(world: dict) -> bool:
    """Scheduler: добить ночной resolve после crash, не открывая новый слот."""
    return normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_RESOLVE


class ActionWindow:
    """Игровые мутации только когда фаза даёт allow_mutations и тик догнан."""

    BLOCKED_MESSAGE = _PLAY_BLOCKED

    @staticmethod
    def allows(*, tick_phase: str | None, incomplete: bool) -> bool:
        if incomplete:
            return False
        return phase_capabilities(tick_phase).allow_mutations

    @classmethod
    def require(cls, *, tick_phase: str | None, incomplete: bool) -> None:
        if not cls.allows(tick_phase=tick_phase, incomplete=incomplete):
            raise ValueError(cls.BLOCKED_MESSAGE)

    @staticmethod
    def allows_orders(*, tick_phase: str | None, incomplete: bool) -> bool:
        """Заявки (будущий declare-then-resolve). Live-фазы сюда не пускают."""
        if incomplete:
            return False
        return phase_capabilities(tick_phase).allow_orders


class TickPipeline:
    """Конвейер фаз: live resolve → economy → play; orders не активируется."""

    LIVE_SEQUENCE: tuple[str, ...] = (
        TICK_PHASE_RESOLVE,
        TICK_PHASE_ECONOMY,
        TICK_PHASE_PLAY,
    )
    # Полный целевой конвейер (orders не live): clock → orders → resolve → economy → play
    TARGET_SEQUENCE: tuple[str, ...] = (
        TICK_PHASE_ORDERS,
        TICK_PHASE_RESOLVE,
        TICK_PHASE_ECONOMY,
        TICK_PHASE_PLAY,
    )

    @staticmethod
    def economy_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_ECONOMY}

    @staticmethod
    def play_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_PLAY}

    @staticmethod
    def orders_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_ORDERS}

    @staticmethod
    def resolve_fields() -> dict[str, str]:
        return {"tick_phase": TICK_PHASE_RESOLVE}

    @staticmethod
    def next_live_phase(current: str | None) -> str | None:
        """Следующая live-фаза или None, если уже play / неизвестно."""
        phase = normalize_tick_phase(current)
        if phase == TICK_PHASE_RESOLVE:
            return TICK_PHASE_ECONOMY
        if phase == TICK_PHASE_ECONOMY:
            return TICK_PHASE_PLAY
        return None

    @staticmethod
    def fields_for(phase: str) -> dict[str, str]:
        if phase not in ALL_TICK_PHASES:
            raise ValueError(f"Неизвестная фаза тика: {phase}")
        return {"tick_phase": phase}
