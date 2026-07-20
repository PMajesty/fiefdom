"""RU/emoji форматтеры и тексты ошибок для ресурсов."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import balance as B
from app.domain import resource_registry as reg
from app.domain.resource_registry import raid_lootable_defs, resource_by_key


# Ключи lootable тройки: старый код всегда печатал оба, даже при 0.
_TRIAD_LOOT_ALWAYS_SHOW = frozenset({B.RES_GRAIN, B.RES_GOODS})


def resource_name_ru(key: str) -> str:
    return resource_by_key(key).name_ru


def synonym_to_key(*, tradeable_only: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in reg.RESOURCE_DEFS:
        if tradeable_only and not r.tradeable:
            continue
        for syn in r.synonyms:
            out[syn.lower()] = r.key
    return out


def tradeable_synonym_alternatives() -> str:
    parts: list[str] = []
    for r in reg.RESOURCE_DEFS:
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
    names = [r.name_ru_object for r in reg.RESOURCE_DEFS]
    if not names:
        return "Нельзя собрать ресурс"
    return f"Можно собрать {_join_object_names(names, conj='или')}"


def trade_forbidden_message() -> str:
    """'Можно менять только зерно и товары' для текущих tradeable."""
    names = [r.name_ru_object for r in reg.RESOURCE_DEFS if r.tradeable]
    return f"Можно менять только {_join_object_names(names, conj='и')}"


def send_forbidden_message() -> str:
    """'Можно передать только зерно или товары' для текущих tradeable."""
    names = [r.name_ru_object for r in reg.RESOURCE_DEFS if r.tradeable]
    return f"Можно передать только {_join_object_names(names, conj='или')}"


def format_status_stash_line(row: Mapping[str, Any], *, defense: int | float) -> str:
    parts = [
        f"{r.status_emoji} {int(row.get(r.key, 0) or 0)}" for r in reg.RESOURCE_DEFS
    ]
    parts.append(f"🛡 {defense}")
    return " · ".join(parts)


def format_daily_production_line(prod_bag: Mapping[str, float]) -> str:
    chunks = [
        f"+{float(prod_bag.get(r.key, 0) or 0):.0f} {r.name_ru_genitive}/день"
        for r in reg.RESOURCE_DEFS
    ]
    return ", ".join(chunks)


def format_prod_parts(prod_bag: Mapping[str, float], *, defense: float = 0.0) -> list[str]:
    """Части строки производства (holdings/статус); только ненулевые."""
    parts: list[str] = []
    for r in reg.RESOURCE_DEFS:
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
        for r in reg.RESOURCE_DEFS
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
