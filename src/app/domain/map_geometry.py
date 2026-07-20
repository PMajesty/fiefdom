"""Тороидальная геометрия карты: расстояния, руины, клейм-соседи."""
from __future__ import annotations

from app import balance as B
from app.domain.production import TileView


def toroidal_delta(a: int, b: int, size: int) -> int:
    """Кратчайшая разница координат на торе размера size."""
    if size <= 0:
        return abs(a - b)
    d = abs(a - b) % size
    return min(d, size - d)


def toroidal_manhattan(
    x1: int, y1: int, x2: int, y2: int, width: int, height: int
) -> int:
    return toroidal_delta(x1, x2, width) + toroidal_delta(y1, y2, height)


def too_close_to_ruins(
    x: int,
    y: int,
    ruins: list[tuple[int, int]],
    width: int,
    height: int,
    min_distance: int = B.RUINS_SPAWN_MIN_DISTANCE,
) -> bool:
    """True, если клетка на руинах или ближе min_distance по тору."""
    if min_distance <= 0 or not ruins:
        return False
    return any(
        toroidal_manhattan(x, y, rx, ry, width, height) < min_distance
        for rx, ry in ruins
    )


def wrap_xy(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    return (x % width, y % height)


def pick_max_separated_tiles(
    candidates: list[dict],
    anchors: list[tuple[int, int]],
    width: int,
    height: int,
    count: int,
) -> list[dict]:
    """Жадный farthest-point на торе: максимизирует мин. расстояние до якорей и уже выбранных."""
    if count <= 0 or not candidates or width <= 0 or height <= 0:
        return []
    active_anchors: list[tuple[int, int]] = list(anchors)
    remaining = list(candidates)
    picked: list[dict] = []
    while remaining and len(picked) < count:

        def score(t: dict) -> tuple[int, int, int]:
            if not active_anchors:
                return (10**9, -int(t["y"]), -int(t["x"]))
            d = min(
                toroidal_manhattan(int(t["x"]), int(t["y"]), ax, ay, width, height)
                for ax, ay in active_anchors
            )
            return (d, -int(t["y"]), -int(t["x"]))

        best = max(remaining, key=score)
        picked.append(best)
        active_anchors.append((int(best["x"]), int(best["y"])))
        best_id = best.get("id")
        if best_id is not None:
            remaining = [t for t in remaining if t.get("id") != best_id]
        else:
            remaining = [t for t in remaining if t is not best]
    return picked


def adjacent_claimable(
    owned: set[tuple[int, int]],
    all_tiles: dict[tuple[int, int], TileView],
    *,
    width: int,
    height: int,
    for_fief_id: int | None = None,
) -> set[tuple[int, int]]:
    """Соседние клетки с учётом тороидальной карты (края смыкаются)."""
    out: set[tuple[int, int]] = set()
    if width <= 0 or height <= 0:
        return out
    for x, y in owned:
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            p = wrap_xy(x + dx, y + dy, width, height)
            t = all_tiles.get(p)
            if not t:
                continue
            # свободно или заросшее чужое
            if t.owner_fief_id is None:
                out.add(p)
            elif t.is_overgrown and t.owner_fief_id != for_fief_id:
                out.add(p)
    return out
