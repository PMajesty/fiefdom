"""Идентичность миров и долин: континент vs инстанс, один fief на world.

Правило продукта: одна усадьба на континент (UNIQUE user_id, world_id).
Временные high-risk зоны в будущем - отдельные worlds (instance) с parent_world_id,
а не вторая усадьба на том же continent. Live-создание долин всегда continent+valley.
"""
from __future__ import annotations

from typing import Any

WORLD_KIND_CONTINENT = "continent"
WORLD_KIND_INSTANCE = "instance"

REALM_KIND_VALLEY = "valley"
REALM_KIND_EXPEDITION = "expedition"

CLOCK_MODE_SHARED = "shared"
CLOCK_MODE_INDEPENDENT = "independent"

LIVE_WORLD_KINDS: frozenset[str] = frozenset(
    {WORLD_KIND_CONTINENT, WORLD_KIND_INSTANCE}
)
LIVE_REALM_KINDS: frozenset[str] = frozenset(
    {REALM_KIND_VALLEY, REALM_KIND_EXPEDITION}
)


def normalize_world_kind(raw: str | None) -> str:
    if raw == WORLD_KIND_INSTANCE:
        return WORLD_KIND_INSTANCE
    return WORLD_KIND_CONTINENT


def normalize_realm_kind(raw: str | None) -> str:
    if raw == REALM_KIND_EXPEDITION:
        return REALM_KIND_EXPEDITION
    return REALM_KIND_VALLEY


def normalize_clock_mode(raw: str | None) -> str:
    if raw == CLOCK_MODE_INDEPENDENT:
        return CLOCK_MODE_INDEPENDENT
    return CLOCK_MODE_SHARED


def is_continent_world(world: dict[str, Any] | None) -> bool:
    if not world:
        return False
    return normalize_world_kind(world.get("world_kind")) == WORLD_KIND_CONTINENT


def is_instance_world(world: dict[str, Any] | None) -> bool:
    if not world:
        return False
    return normalize_world_kind(world.get("world_kind")) == WORLD_KIND_INSTANCE


def shares_continent_clock(realm: dict[str, Any] | None) -> bool:
    """Долина зеркалит часы мира (sync_realms_clock_from_world фильтр)."""
    if not realm:
        return False
    return normalize_clock_mode(realm.get("clock_mode")) == CLOCK_MODE_SHARED


def world_expired(world: dict[str, Any] | None, *, tick_index: int) -> bool:
    """Presence: expires_tick задан и tick_index уже достиг/прошёл его."""
    if not world:
        return False
    expires = world.get("expires_tick")
    if expires is None:
        return False
    return int(tick_index) >= int(expires)


def realm_expired(realm: dict[str, Any] | None, *, tick_index: int) -> bool:
    if not realm:
        return False
    expires = realm.get("expires_tick")
    if expires is None:
        return False
    return int(tick_index) >= int(expires)


def feature_flag_enabled(realm: dict[str, Any] | None, flag: str) -> bool:
    """Читает realms.feature_flags; отсутствует/False = выключено."""
    if not realm:
        return False
    flags = realm.get("feature_flags") or {}
    if not isinstance(flags, dict):
        return False
    return bool(flags.get(flag))


def second_fief_on_world_message() -> str:
    """Стабильный текст отказа (byte-identical с текущим join_fief)."""
    return (
        "У вас уже есть усадьба на континенте. "
        "Вторая усадьба недоступна."
    )
