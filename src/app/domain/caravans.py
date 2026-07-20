"""Караваны: declare → эскроу → ночной resolve (как набеги)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any

from app import balance as B
from app.domain.raids import RaidNightPartyNotice
from app.domain.resource_bags import LootBag, empty_loot_bag
from app.domain.resource_registry import raid_lootable_keys, tradeable_keys


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


# Обоз в пути: ещё можно лутать ночным набегом (после midday - locked).
ROAD_CARAVAN_STATUSES = frozenset({"open", "locked"})


@dataclass(frozen=True)
class CaravanEscrowDebit:
    """План списания с одного обоза в пути при набеге."""

    intent_id: int
    res: str
    taken: int
    remaining_amt: int


def caravan_is_public(amt: int) -> bool:
    return int(amt) >= int(B.CARAVAN_PUBLIC_AMOUNT)


def open_caravan_escrow_bag(
    intents: Sequence[Mapping[str, Any]],
    *,
    loot_keys: Sequence[str] | None = None,
) -> LootBag:
    """Сумма груза исходящих обозов в пути (open/locked; без амбарной защиты)."""
    keys = tuple(loot_keys) if loot_keys is not None else raid_lootable_keys()
    allowed = set(keys) & set(tradeable_keys())
    bag = empty_loot_bag()
    for intent in intents:
        if str(intent.get("status") or "") not in ROAD_CARAVAN_STATUSES:
            continue
        if str(intent.get("kind") or "") != "caravan":
            continue
        payload = intent.get("payload") or {}
        res = str(payload.get("res") or "")
        amt = int(payload.get("amt") or 0)
        if res not in allowed or amt <= 0:
            continue
        bag[res] = int(bag.get(res, 0) or 0) + amt
    return bag


def plan_caravan_escrow_debits(
    intents: Sequence[Mapping[str, Any]],
    take: Mapping[str, int],
) -> list[CaravanEscrowDebit]:
    """Списывает take с обозов по возрастанию id; remaining_amt=0 значит отмена."""
    need = {
        str(k): max(0, int(v or 0))
        for k, v in take.items()
        if max(0, int(v or 0)) > 0
    }
    if not need:
        return []
    ordered = sorted(
        (
            i
            for i in intents
            if str(i.get("status") or "") in ROAD_CARAVAN_STATUSES
            and str(i.get("kind") or "") == "caravan"
        ),
        key=lambda row: int(row["id"]),
    )
    plan: list[CaravanEscrowDebit] = []
    for intent in ordered:
        if not need:
            break
        payload = intent.get("payload") or {}
        res = str(payload.get("res") or "")
        amt = int(payload.get("amt") or 0)
        want = int(need.get(res, 0) or 0)
        if want <= 0 or amt <= 0:
            continue
        taken = min(amt, want)
        remaining = amt - taken
        plan.append(
            CaravanEscrowDebit(
                intent_id=int(intent["id"]),
                res=res,
                taken=taken,
                remaining_amt=remaining,
            )
        )
        left = want - taken
        if left <= 0:
            need.pop(res, None)
        else:
            need[res] = left
    return plan


def format_caravan_intercepted_gate_line() -> str:
    return "Обоз перехвачен у ворот."


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
