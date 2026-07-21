"""Тексты и правила голода, общие для статуса, владений и действий."""
from __future__ import annotations

from app import balance as B


def hunger_status_alert() -> str:
    return (
        "Голод: земли нечем кормить. Урожай вдвое, сила со стороны не копится "
        f"(двор добирает до {B.MILITIA_FREE}), воевать нельзя. "
        "Можно распустить дружину или собрать зерно."
    )


def hunger_holdings_banner() -> str:
    return (
        "Голод: урожай с земли снижен вдвое; сила со стороны не копится "
        f"(двор добирает до {B.MILITIA_FREE})."
    )


def gather_might_hungry_message() -> str:
    return "Голодные мужики не набирают силу"
