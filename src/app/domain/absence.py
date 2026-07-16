"""Правила отсутствия: дремлет / заросшие и компенсация за клейм."""
from __future__ import annotations

from app import balance as B


def inactivity_tier(ticks: int) -> str:
    if ticks >= B.OVERGROWN_TICKS:
        return "overgrown"
    if ticks >= B.DORMANT_TICKS:
        return "dormant"
    return "ok"


def compensation_for_claim(claim_price: int) -> int:
    return int(claim_price * B.OVERGROWN_COMPENSATION)
