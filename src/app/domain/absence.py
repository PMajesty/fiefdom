"""Правила отсутствия: дремлет / заросшие и компенсация за клейм."""
from __future__ import annotations

from app import balance as B


def inactivity_tier(days: int) -> str:
    if days >= B.OVERGROWN_DAYS:
        return "overgrown"
    if days >= B.DORMANT_DAYS:
        return "dormant"
    return "ok"


def compensation_for_claim(claim_price: int) -> int:
    return int(claim_price * B.OVERGROWN_COMPENSATION)
