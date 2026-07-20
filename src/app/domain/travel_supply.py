"""Снабжение похода: чтение fee из payload и короткие подписи для UI."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import balance as B

PAYLOAD_SUPPLY_GRAIN = "supply_grain"


def intent_supply_grain(payload: Mapping[str, Any] | None) -> int:
    """Сколько зерна уже списано на заявку; нет поля - 0 (старые заявки)."""
    if not payload:
        return 0
    return max(0, int(payload.get(PAYLOAD_SUPPLY_GRAIN) or 0))


def travel_supply_net_delta(*, prior_fee: int, new_fee: int) -> int:
    """>0 доплатить, <0 вернуть, 0 без движения зерна."""
    return int(new_fee) - int(prior_fee)


def format_travel_supply_charge_line(*, new_fee: int, prior_fee: int = 0) -> str:
    """Подпись платы: полный сбор или нетто при смене уже открытой заявки."""
    fee = max(0, int(new_fee))
    prior = max(0, int(prior_fee))
    delta = travel_supply_net_delta(prior_fee=prior, new_fee=fee)
    if prior <= 0:
        return (
            f"Снабжение похода: {fee} зерна "
            "(отдельно от дневного корма дома)."
        )
    if delta == 0:
        return (
            f"Снабжение похода: без доплаты "
            f"({fee} зерна уже списаны)."
        )
    if delta > 0:
        return (
            f"Снабжение похода: доплата {delta} зерна "
            f"(всего {fee})."
        )
    return (
        f"Снабжение похода: возврат {-delta} зерна "
        f"(останется {fee})."
    )


def format_travel_supply_confirm_line(might: int) -> str:
    """Первый выход (набег или новая застава без открытой стойки)."""
    return format_travel_supply_charge_line(
        new_fee=B.travel_supply_grain(might), prior_fee=0
    )
