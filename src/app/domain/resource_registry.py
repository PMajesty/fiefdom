"""Реестр ресурсов и маппинг колонок stash/pending."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

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


def stash_columns(bag: Mapping[str, int | float]) -> dict[str, int]:
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
