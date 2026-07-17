"""Реестр ресурсов и сумка: структура отдельно от чисел в balance."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app import balance as B


@dataclass(frozen=True)
class ResourceDef:
    """Описание ресурса: ключ = колонка stash; числа берутся из balance."""

    key: str
    name_ru: str
    name_ru_genitive: str
    name_ru_object: str  # винительный в ошибках сбора: зерно/товары/силу
    synonyms: tuple[str, ...]
    tradeable: bool
    stash_capped: bool
    raid_lootable: bool
    status_emoji: str
    raid_stolen_column: str | None = None

    @property
    def pending_column(self) -> str:
        return f"pending_{self.key}"


# Единственный источник структуры и отображаемых имён.
RESOURCE_DEFS: tuple[ResourceDef, ...] = (
    ResourceDef(
        key=B.RES_GRAIN,
        name_ru="Зерно",
        name_ru_genitive="зерна",
        name_ru_object="зерно",
        synonyms=("зерно", "grain"),
        tradeable=True,
        stash_capped=True,
        raid_lootable=True,
        raid_stolen_column="grain_stolen",
        status_emoji="🌾",
    ),
    ResourceDef(
        key=B.RES_GOODS,
        name_ru="Товары",
        name_ru_genitive="товаров",
        name_ru_object="товары",
        synonyms=("товары", "goods"),
        tradeable=True,
        stash_capped=True,
        raid_lootable=True,
        raid_stolen_column="goods_stolen",
        status_emoji="📦",
    ),
    ResourceDef(
        key=B.RES_MIGHT,
        name_ru="Сила",
        name_ru_genitive="силы",
        name_ru_object="силу",
        synonyms=("сила", "might"),
        tradeable=False,
        stash_capped=False,
        raid_lootable=False,
        raid_stolen_column=None,
        status_emoji="⚔️",
    ),
)

# Ключи lootable тройки: старый код всегда печатал оба, даже при 0.
_TRIAD_LOOT_ALWAYS_SHOW = frozenset({B.RES_GRAIN, B.RES_GOODS})


def resource_defs() -> tuple[ResourceDef, ...]:
    return tuple(RESOURCE_DEFS)


def live_resource_keys() -> tuple[str, ...]:
    return tuple(r.key for r in RESOURCE_DEFS)


def resource_by_key(key: str) -> ResourceDef:
    for r in RESOURCE_DEFS:
        if r.key == key:
            return r
    raise KeyError(key)


def tradeable_keys() -> tuple[str, ...]:
    return tuple(r.key for r in RESOURCE_DEFS if r.tradeable)


def stash_capped_keys() -> frozenset[str]:
    return frozenset(r.key for r in RESOURCE_DEFS if r.stash_capped)


def uncapped_keys() -> frozenset[str]:
    return frozenset(live_resource_keys()) - stash_capped_keys()


def raid_lootable_defs() -> tuple[ResourceDef, ...]:
    return tuple(r for r in RESOURCE_DEFS if r.raid_lootable and r.raid_stolen_column)


def raid_lootable_keys() -> tuple[str, ...]:
    return tuple(r.key for r in raid_lootable_defs())


def pending_column_for(key: str) -> str:
    return f"pending_{key}"


def resource_name_ru(key: str) -> str:
    return resource_by_key(key).name_ru


def synonym_to_key(*, tradeable_only: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in RESOURCE_DEFS:
        if tradeable_only and not r.tradeable:
            continue
        for syn in r.synonyms:
            out[syn.lower()] = r.key
    return out


def tradeable_synonym_alternatives() -> str:
    parts: list[str] = []
    for r in RESOURCE_DEFS:
        if not r.tradeable:
            continue
        parts.extend(r.synonyms)
    return "|".join(parts)


def _join_object_names(names: list[str], *, conj: str) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} {conj} {names[-1]}"


def gather_forbidden_message() -> str:
    """Текст ошибки сбора; для текущей тройки - прежняя фраза байт-в-байт."""
    names = [r.name_ru_object for r in RESOURCE_DEFS]
    if not names:
        return "Нельзя собрать ресурс"
    return f"Можно собрать {_join_object_names(names, conj='или')}"


def trade_forbidden_message() -> str:
    """'Можно менять только зерно и товары' для текущих tradeable."""
    names = [r.name_ru_object for r in RESOURCE_DEFS if r.tradeable]
    return f"Можно менять только {_join_object_names(names, conj='и')}"


def send_forbidden_message() -> str:
    """'Можно передать только зерно или товары' для текущих tradeable."""
    names = [r.name_ru_object for r in RESOURCE_DEFS if r.tradeable]
    return f"Можно передать только {_join_object_names(names, conj='или')}"


ResourceBag = dict[str, int]
PendingBag = dict[str, float]
LootBag = dict[str, int]


def empty_stash() -> ResourceBag:
    return {key: 0 for key in live_resource_keys()}


def empty_pending() -> PendingBag:
    return {key: 0.0 for key in live_resource_keys()}


def empty_loot_bag() -> LootBag:
    return {key: 0 for key in raid_lootable_keys()}


def stash_from_row(row: Mapping[str, Any]) -> ResourceBag:
    return {key: int(row.get(key, 0) or 0) for key in live_resource_keys()}


def pending_from_row(row: Mapping[str, Any]) -> PendingBag:
    return {
        key: float(row.get(pending_column_for(key), 0) or 0)
        for key in live_resource_keys()
    }


def stash_columns(bag: Mapping[str, int | float]) -> ResourceBag:
    """Bag → kwargs колонок stash. Только live-ключи."""
    return {key: int(bag.get(key, 0) or 0) for key in live_resource_keys()}


def pending_columns(bag: Mapping[str, int | float]) -> dict[str, float]:
    return {
        pending_column_for(key): float(bag.get(key, 0) or 0)
        for key in live_resource_keys()
    }


def fief_balance_columns(
    stash: Mapping[str, int | float],
    pending: Mapping[str, int | float],
) -> dict[str, int | float]:
    out: dict[str, int | float] = dict(stash_columns(stash))
    out.update(pending_columns(pending))
    return out


def migrate_row_balances(row: Mapping[str, Any]) -> dict[str, int | float]:
    """Идемпотентный round-trip колонки → bag → колонки без смены значений."""
    return fief_balance_columns(stash_from_row(row), pending_from_row(row))


def add_bags(
    left: Mapping[str, float], right: Mapping[str, float]
) -> dict[str, float]:
    return {
        key: float(left.get(key, 0) or 0) + float(right.get(key, 0) or 0)
        for key in live_resource_keys()
    }


def scale_bag(bag: Mapping[str, float], mult: float) -> dict[str, float]:
    return {
        key: float(bag.get(key, 0) or 0) * mult for key in live_resource_keys()
    }


def capped_pending_add(current: float, produced: float, cap_days: int) -> float:
    """Кап неубранного: как в apply_fief_tick при produced > 0."""
    if produced > 0:
        return min(produced * cap_days, current + produced)
    return current


def apply_production_to_pending(
    pending: Mapping[str, float],
    production: Mapping[str, float],
    cap_days: int,
) -> PendingBag:
    return {
        key: capped_pending_add(
            float(pending.get(key, 0) or 0),
            float(production.get(key, 0) or 0),
            cap_days,
        )
        for key in live_resource_keys()
    }


def normalize_debit_amounts(
    amounts: Mapping[str, int] | None = None,
    **kwargs: int,
) -> ResourceBag:
    merged: dict[str, int] = {}
    if amounts:
        for key, raw in amounts.items():
            merged[str(key)] = int(raw)
    for key, raw in kwargs.items():
        merged[str(key)] = int(raw)
    if not merged:
        raise ValueError("debit_fief_resources: пустой списанный набор")
    live = set(live_resource_keys())
    out: ResourceBag = {}
    for key, amt in merged.items():
        if key not in live:
            raise ValueError(f"debit_fief_resources: колонка {key}")
        if amt <= 0:
            raise ValueError("debit_fief_resources: сумма должна быть > 0")
        out[key] = amt
    return out


normalize_credit_amounts = normalize_debit_amounts


def apply_gather_to_stash(
    stash: Mapping[str, int],
    resource: str,
    amount: int,
    *,
    cap: int,
) -> tuple[ResourceBag, int]:
    if resource not in live_resource_keys():
        raise ValueError(f"Нельзя собрать: {resource}")
    out = stash_columns(stash)
    qty = int(amount)
    if resource in uncapped_keys():
        out[resource] = out[resource] + qty
        return out, qty
    room = max(0, int(cap) - out[resource])
    gained = min(qty, room)
    out[resource] = out[resource] + gained
    return out, gained


def stash_amount(row: Mapping[str, Any], resource: str) -> int:
    return int(row.get(resource, 0) or 0)


def capped_receive_amount(held: int, amount: int, cap: int) -> int:
    return min(int(amount), max(0, int(cap) - int(held)))


def format_status_stash_line(row: Mapping[str, Any], *, defense: int | float) -> str:
    parts = [
        f"{r.status_emoji} {int(row.get(r.key, 0) or 0)}" for r in RESOURCE_DEFS
    ]
    parts.append(f"🛡 {defense}")
    return " · ".join(parts)


def format_daily_production_line(prod_bag: Mapping[str, float]) -> str:
    chunks = [
        f"+{float(prod_bag.get(r.key, 0) or 0):.0f} {r.name_ru_genitive}/день"
        for r in RESOURCE_DEFS
    ]
    return ", ".join(chunks)


def format_prod_parts(prod_bag: Mapping[str, float], *, defense: float = 0.0) -> list[str]:
    """Части строки производства (holdings/статус); только ненулевые."""
    parts: list[str] = []
    for r in RESOURCE_DEFS:
        amt = float(prod_bag.get(r.key, 0) or 0)
        if amt:
            parts.append(f"+{amt:.0f} {r.name_ru_genitive}/день")
    if defense:
        parts.append(f"+{float(defense):.0f} защиты")
    return parts


def format_totals_production_line(
    prod_bag: Mapping[str, float], *, defense: float
) -> str:
    """Итоговая строка владений: байт-идентична для тройки."""
    chunks = [
        f"+{float(prod_bag.get(r.key, 0) or 0):.0f} {r.name_ru_genitive}/день"
        for r in RESOURCE_DEFS
    ]
    return f"Итого: {', '.join(chunks)} · защита {float(defense):.0f}"


def gather_result_text(resource: str, gained: int, amount: int) -> str:
    rdef = resource_by_key(resource)
    if not rdef.stash_capped:
        return f"Сбор: +{gained} {rdef.name_ru_genitive} (−1 действие)."
    suffix = "" if gained == amount else " (склад почти полон)"
    return f"Сбор: +{gained} {rdef.name_ru_genitive} (−1 действие).{suffix}"


def format_attacker_loot_suffix(stolen: Mapping[str, int]) -> str:
    """'+3 зерна, +1 товаров.' - тройка всегда оба ключа; прочие lootable без нулей."""
    parts: list[str] = []
    for r in raid_lootable_defs():
        amt = int(stolen.get(r.key, 0) or 0)
        if amt == 0 and r.key not in _TRIAD_LOOT_ALWAYS_SHOW:
            continue
        parts.append(f"+{amt} {r.name_ru_genitive}")
    return ", ".join(parts) + "."


def format_victim_loot_sentence(stolen: Mapping[str, int]) -> str:
    """'Унесено 3 зерна и 1 товаров.' - тройка всегда оба ключа; прочие без нулей."""
    parts: list[str] = []
    for r in raid_lootable_defs():
        amt = int(stolen.get(r.key, 0) or 0)
        if amt == 0 and r.key not in _TRIAD_LOOT_ALWAYS_SHOW:
            continue
        parts.append(f"{amt} {r.name_ru_genitive}")
    if not parts:
        return "Унесено 0."
    if len(parts) == 1:
        return f"Унесено {parts[0]}."
    if len(parts) == 2:
        return f"Унесено {parts[0]} и {parts[1]}."
    return f"Унесено {', '.join(parts[:-1])} и {parts[-1]}."
