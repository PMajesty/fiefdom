"""Караваны: declare → эскроу → midday confirm → ночной resolve."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any

from app import balance as B
from app.domain.raids import RaidNightPartyNotice
from app.domain.resource_bags import LootBag, empty_loot_bag
from app.domain.resource_registry import (
    raid_lootable_keys,
    resource_by_key,
    tradeable_keys,
)


@dataclass
class DeclareCaravanResult:
    """Итог declare_caravan для хендлера (только подтверждение отправителю)."""

    intent_id: int
    receiver_fief_id: int
    receiver_name: str
    res: str
    amt: int
    is_public: bool
    dm_text: str


@dataclass
class LockCaravanReport:
    """Midday-confirm: notices + ids; флаги коммитятся после попытки доставки."""

    announced_intent_count: int = 0
    notices: list[RaidNightPartyNotice] = field(default_factory=list)
    intent_ids: tuple[int, ...] = ()
    public_ids: tuple[int, ...] = ()


@dataclass
class ResolveCaravanReport:
    resolved_count: int = 0
    notices: list[RaidNightPartyNotice] = field(default_factory=list)
    digest_lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CaravanRouteBundle:
    """Сводка обозов одного отправителя к одному получателю."""

    sender_fief_id: int
    receiver_fief_id: int
    sender_realm_id: int
    receiver_realm_id: int
    amounts: Mapping[str, int]
    intent_ids: tuple[int, ...]

    @property
    def total_amt(self) -> int:
        return sum(int(v) for v in self.amounts.values())


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


def caravan_lock_notified(payload: Mapping[str, Any] | None) -> bool:
    return bool((payload or {}).get("lock_notified"))


def format_caravan_cargo_parts(amounts: Mapping[str, int]) -> str:
    """'12 зерна, 8 товаров' в порядке tradeable registry."""
    parts: list[str] = []
    for key in tradeable_keys():
        amt = int(amounts.get(key, 0) or 0)
        if amt <= 0:
            continue
        parts.append(f"{amt} {resource_by_key(key).name_ru_genitive}")
    for key, amt in amounts.items():
        if key in tradeable_keys():
            continue
        n = int(amt or 0)
        if n <= 0:
            continue
        parts.append(f"{n} {resource_name_fallback(key)}")
    if not parts:
        return "груз"
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} и {parts[-1]}"


def resource_name_fallback(key: str) -> str:
    try:
        return resource_by_key(key).name_ru_genitive
    except KeyError:
        return str(key)


def group_caravan_routes(
    intents: Sequence[Mapping[str, Any]],
) -> list[CaravanRouteBundle]:
    """Группирует обозы по паре отправитель→получатель."""
    buckets: dict[tuple[int, int], dict[str, Any]] = {}
    order: list[tuple[int, int]] = []
    for intent in intents:
        payload = intent.get("payload") or {}
        sender_id = int(intent.get("fief_id") or 0)
        receiver_id = int(payload.get("receiver_id") or 0)
        res = str(payload.get("res") or "")
        amt = int(payload.get("amt") or 0)
        if sender_id <= 0 or receiver_id <= 0 or amt <= 0:
            continue
        key = (sender_id, receiver_id)
        if key not in buckets:
            buckets[key] = {
                "amounts": defaultdict(int),
                "intent_ids": [],
                "sender_realm_id": int(payload.get("sender_realm_id") or 0),
                "receiver_realm_id": int(payload.get("receiver_realm_id") or 0),
            }
            order.append(key)
        bucket = buckets[key]
        if res:
            bucket["amounts"][res] += amt
        bucket["intent_ids"].append(int(intent["id"]))
        if not bucket["sender_realm_id"]:
            bucket["sender_realm_id"] = int(payload.get("sender_realm_id") or 0)
        if not bucket["receiver_realm_id"]:
            bucket["receiver_realm_id"] = int(
                payload.get("receiver_realm_id") or 0
            )
    out: list[CaravanRouteBundle] = []
    for key in order:
        bucket = buckets[key]
        out.append(
            CaravanRouteBundle(
                sender_fief_id=key[0],
                receiver_fief_id=key[1],
                sender_realm_id=int(bucket["sender_realm_id"]),
                receiver_realm_id=int(bucket["receiver_realm_id"]),
                amounts=dict(bucket["amounts"]),
                intent_ids=tuple(bucket["intent_ids"]),
            )
        )
    return out


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


def format_caravan_lock_public(
    sender_name: str, receiver_name: str, cargo: str
) -> str:
    sender = escape(str(sender_name))
    receiver = escape(str(receiver_name))
    return (
        f"📦 Обоз: {sender} шлёт {cargo} "
        f"усадьбе {receiver} - в пути до колокола."
    )


def format_caravan_lock_receiver_dm(sender_name: str, cargo: str) -> str:
    sender = escape(str(sender_name))
    return (
        f"К вам идёт обоз от {sender}: {cargo}. "
        f"Прибудет после колокола тика."
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
