"""Караваны: declare → эскроу → ночной resolve (как набеги)."""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape

from app import balance as B
from app.domain.raids import RaidNightPartyNotice


@dataclass
class DeclareCaravanResult:
    """Итог declare_caravan для хендлера (без доставки)."""

    intent_id: int
    receiver_fief_id: int
    receiver_name: str
    res: str
    amt: int
    is_public: bool
    dm_text: str
    receiver_dm_text: str
    public_declare_text: str | None = None


@dataclass
class ResolveCaravanReport:
    resolved_count: int = 0
    notices: list[RaidNightPartyNotice] = field(default_factory=list)
    digest_lines: list[tuple[int, str]] = field(default_factory=list)


def caravan_is_public(amt: int) -> bool:
    return int(amt) >= int(B.CARAVAN_PUBLIC_AMOUNT)


def format_caravan_declare_public(
    sender_name: str, receiver_name: str, amt: int, res_name: str
) -> str:
    sender = escape(str(sender_name))
    receiver = escape(str(receiver_name))
    return (
        f"📦 Обоз: {sender} шлёт {int(amt)} {res_name} "
        f"усадьбе {receiver} - в пути до колокола."
    )


def format_caravan_land_public(
    sender_name: str, receiver_name: str, amt: int, res_name: str
) -> str:
    sender = escape(str(sender_name))
    receiver = escape(str(receiver_name))
    return (
        f"📦 Обоз дошёл: {sender} → {receiver}, "
        f"{int(amt)} {res_name}."
    )


def format_caravan_bounce_public(
    sender_name: str, receiver_name: str, amt: int, res_name: str
) -> str:
    sender = escape(str(sender_name))
    receiver = escape(str(receiver_name))
    return (
        f"📦 Обоз вернулся: от {sender} к {receiver} "
        f"не приняли {int(amt)} {res_name}."
    )
