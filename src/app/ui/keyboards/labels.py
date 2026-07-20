"""Подписи кнопок клавиатур (стоимость, координаты, тип здания)."""
from __future__ import annotations

from app import balance as B
from app.domain.map_gen import coord_label


def format_claim_button(
    x: int,
    y: int,
    tile_type: str,
    next_tile_count: int,
    *,
    is_overgrown: bool = False,
) -> str:
    """Подпись кнопки занятия: \"А3 Поле · 30 тов.\" (глушь ×2, кроме заросших)."""
    name = B.TILE_NAMES_RU.get(tile_type, tile_type)
    is_wilds = (not is_overgrown) and tile_type == B.TILE_WILDS
    cost = B.claim_cost(next_tile_count, is_wilds=is_wilds)
    return f"{coord_label(x, y)} {name} · {cost} тов."


def format_building_type_label(
    building: str,
    tiles: list[dict] | None = None,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Подпись типа здания с минимальной реальной ценой по клеткам усадьбы."""
    name = B.BUILDING_NAMES_RU.get(building, building)
    if tiles is None:
        cost = B.scaled_building_cost(B.building_upgrade_cost(building, 1), cost_mult)
        return f"{name} · {cost} тов."
    cost = B.cheapest_build_action_cost(building, tiles, cost_mult=cost_mult)
    if cost is not None:
        return f"{name} · {cost} тов."
    has_maxed = any(
        t.get("building") == building
        and int(t.get("building_level") or 0) >= 3
        and not t.get("damaged")
        for t in tiles
    )
    if has_maxed:
        return f"{name} · макс."
    return name


def format_build_cost_label(
    building: str,
    tile: dict,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Стоимость постройки/апгрейда/ремонта на клетке."""
    current = tile.get("building")
    level = int(tile.get("building_level") or 0)
    damaged = bool(tile.get("damaged"))
    if damaged and current:
        return f"{B.repair_cost(current, level)} тов."
    if current and current != building:
        return "занято"
    if not current:
        cost = B.scaled_building_cost(B.building_upgrade_cost(building, 1), cost_mult)
        return f"{cost} тов."
    target = level + 1
    if target > 3:
        return "макс."
    cost = B.scaled_building_cost(B.building_upgrade_cost(building, target), cost_mult)
    return f"{cost} тов."


def format_build_tile_button(
    building: str,
    tile: dict,
    *,
    cost_mult: float = 1.0,
) -> str:
    """Подпись клетки при выборе места стройки."""
    coord = coord_label(tile["x"], tile["y"])
    cost_label = format_build_cost_label(building, tile, cost_mult=cost_mult)
    current = tile.get("building")
    level = int(tile.get("building_level") or 0)
    damaged = bool(tile.get("damaged"))
    if damaged and current:
        bname = B.BUILDING_NAMES_RU.get(current, current)
        return f"{coord} ремонт {bname}{level} · {cost_label}"
    if current and current != building:
        bname = B.BUILDING_NAMES_RU.get(current, current)
        return f"{coord} {bname}{level} · {cost_label}"
    if current:
        return f"{coord} →{level + 1} · {cost_label}"
    return f"{coord} · {cost_label}"
