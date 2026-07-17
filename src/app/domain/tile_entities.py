"""Заглушка до Phase 3: типы для modifiers; реестр kinds пуст."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


ENTITY_KIND_CONTRACTS: dict[str, Any] = {}
TICK_RESOLVE_HANDLERS: dict[str, Any] = {}


@dataclass(frozen=True)
class ActiveTileEntityRef:
    id: int
    kind: str
    x: int
    y: int
    payload: dict[str, Any]
    expires_tick: int | None = None


def modifiers_from_tile_entities(
    entities: Sequence[ActiveTileEntityRef],
    *,
    tick_index: int = 0,
) -> tuple:
    del entities, tick_index
    return ()


def validate_entity_kind_contracts() -> list[str]:
    return []
