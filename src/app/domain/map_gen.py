"""Генерация прямоугольной карты долины."""
from __future__ import annotations

import random
from dataclasses import dataclass

from app import balance as B


@dataclass
class GenTile:
    x: int
    y: int
    tile_type: str
    is_bridge: bool = False
    ruins_looted: bool = False


CYRILLIC_COLS = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"


def col_label(x: int) -> str:
    if x < len(CYRILLIC_COLS):
        return CYRILLIC_COLS[x]
    # запас на рост карты
    return f"А{x}"


def coord_label(x: int, y: int) -> str:
    return f"{col_label(x)}{y + 1}"


def generate_map(width: int, height: int, rng: random.Random | None = None) -> list[GenTile]:
    rng = rng or random.Random()
    grid: dict[tuple[int, int], str] = {}

    # Дорога: горизонталь с одним изгибом
    road_y = height // 2
    bend_x = rng.randint(max(1, width // 4), max(1, width * 3 // 4 - 1))
    bend_dir = rng.choice([-1, 1])
    alt_y = min(height - 1, max(0, road_y + bend_dir))
    for x in range(width):
        y = alt_y if x >= bend_x else road_y
        grid[(x, y)] = B.TILE_ROAD

    # Река: вертикаль, пересечение с дорогой = мост
    river_x = rng.randint(max(1, width // 5), max(1, width * 4 // 5 - 1))
    bridge: tuple[int, int] | None = None
    for y in range(height):
        key = (river_x, y)
        if grid.get(key) == B.TILE_ROAD:
            bridge = key
            grid[key] = B.TILE_ROAD  # мост считается дорогой
        else:
            grid[key] = B.TILE_RIVER
    if bridge is None:
        # гарантируем пересечение
        by = road_y
        bridge = (river_x, by)
        grid[bridge] = B.TILE_ROAD

    weights = list(B.TILE_FILL_WEIGHTS.items())
    types = [t for t, _ in weights]
    wts = [w for _, w in weights]

    def neighbors(x: int, y: int) -> list[str]:
        out = []
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            n = grid.get((x + dx, y + dy))
            if n and n not in (B.TILE_ROAD, B.TILE_RIVER):
                out.append(n)
        return out

    empties = [(x, y) for y in range(height) for x in range(width) if (x, y) not in grid]
    rng.shuffle(empties)
    for x, y in empties:
        placed = neighbors(x, y)
        if placed and rng.random() < B.TILE_CLUSTER_BONUS:
            grid[(x, y)] = rng.choice(placed)
        else:
            grid[(x, y)] = rng.choices(types, weights=wts, k=1)[0]

    tiles = [
        GenTile(
            x=x,
            y=y,
            tile_type=grid[(x, y)],
            is_bridge=(bridge is not None and (x, y) == bridge),
        )
        for y in range(height)
        for x in range(width)
    ]

    # Валидация: достаточно кандидатов на спавн
    spawnable = [
        t
        for t in tiles
        if t.tile_type not in (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER)
    ]
    if len(spawnable) < max(3, (width * height) // 5):
        return generate_map(width, height, rng)
    return tiles


def append_strip(
    width: int,
    height: int,
    existing: list[GenTile],
    axis: str,
    rng: random.Random | None = None,
) -> tuple[list[GenTile], int, int]:
    """Добавляет ряд или колонку свежих клеток. axis: 'row' | 'col'."""
    rng = rng or random.Random()
    new_tiles = list(existing)
    weights = list(B.TILE_FILL_WEIGHTS.items())
    types = [t for t, _ in weights]
    wts = [w for _, w in weights]

    if axis == "row":
        ny = height
        for x in range(width):
            # лёгкий кластер от соседа сверху
            above = next((t.tile_type for t in existing if t.x == x and t.y == height - 1), None)
            if above and above not in (B.TILE_ROAD, B.TILE_RIVER) and rng.random() < B.TILE_CLUSTER_BONUS:
                tt = above
            else:
                tt = rng.choices(types, weights=wts, k=1)[0]
            new_tiles.append(GenTile(x=x, y=ny, tile_type=tt))
        return new_tiles, width, height + 1

    nx = width
    for y in range(height):
        left = next((t.tile_type for t in existing if t.x == width - 1 and t.y == y), None)
        if left and left not in (B.TILE_ROAD, B.TILE_RIVER) and rng.random() < B.TILE_CLUSTER_BONUS:
            tt = left
        else:
            tt = rng.choices(types, weights=wts, k=1)[0]
        new_tiles.append(GenTile(x=nx, y=y, tile_type=tt))
    return new_tiles, width + 1, height
