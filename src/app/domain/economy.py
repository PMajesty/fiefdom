"""Рендер эмодзи-карты и расчёт дневного производства."""
from __future__ import annotations

from dataclasses import dataclass

from app import balance as B
from app.domain.map_gen import col_label


@dataclass
class TileView:
    x: int
    y: int
    tile_type: str
    owner_fief_id: int | None
    building: str | None
    building_level: int
    is_bridge: bool = False
    is_core: bool = False
    is_overgrown: bool = False


@dataclass
class Production:
    grain: float = 0.0
    goods: float = 0.0
    might: float = 0.0
    defense: float = 0.0

    def scale(self, mult: float) -> "Production":
        return Production(
            grain=self.grain * mult,
            goods=self.goods * mult,
            might=self.might * mult,
            defense=self.defense,
        )


def tile_passive(tile_type: str) -> Production:
    if tile_type == B.TILE_RIVER:
        return Production(grain=B.RIVER_PASSIVE_GRAIN)
    if tile_type == B.TILE_ROAD:
        return Production(goods=B.ROAD_PASSIVE_GOODS)
    return Production()


def building_production(building: str, level: int, tile_type: str) -> Production:
    if level <= 0 or not building:
        return Production()
    if building == B.BLD_MANOR:
        return Production(
            grain=float(B.MANOR_GRAIN),
            goods=float(B.MANOR_GOODS),
            might=float(B.MANOR_MIGHT),
        )
    native = B.NATIVE_TILE.get(building)
    bonus = B.NATIVE_BONUS if native and tile_type == native else 1.0
    if building == B.BLD_FARM:
        return Production(grain=B.FARM_YIELD[level] * bonus)
    if building == B.BLD_WORKSHOP:
        return Production(goods=B.WORKSHOP_YIELD[level] * bonus)
    if building == B.BLD_WATCH:
        return Production(
            might=B.WATCH_MIGHT[level] * bonus,
            defense=B.WATCH_DEFENSE[level] * bonus,
        )
    return Production()


def fief_daily_production(
    tiles: list[TileView],
    *,
    hungry: bool = False,
    farm_mult: float = 1.0,
    current_might: int = 0,
) -> Production:
    total = Production()
    active_tiles = 0
    manor_might = 0.0
    for t in tiles:
        if t.is_overgrown:
            continue
        active_tiles += 1
        p = tile_passive(t.tile_type)
        b = building_production(t.building or "", t.building_level, t.tile_type)
        if t.building == B.BLD_FARM:
            b = Production(grain=b.grain * farm_mult, goods=b.goods, might=b.might, defense=b.defense)
        if t.building == B.BLD_MANOR:
            manor_might += b.might
            b = Production(grain=b.grain, goods=b.goods, might=0.0, defense=b.defense)
        total = Production(
            grain=total.grain + p.grain + b.grain,
            goods=total.goods + p.goods + b.goods,
            might=total.might + p.might + b.might,
            defense=total.defense + b.defense,
        )
    # Сила двора только до бесплатного потолка дружины.
    free_room = max(0, B.MILITIA_FREE - max(0, int(current_might)))
    manor_applied = min(manor_might, float(free_room))
    total = Production(
        grain=total.grain,
        goods=total.goods,
        might=total.might + manor_applied,
        defense=total.defense,
    )
    if active_tiles > 0 and B.FIEF_BASE_GOODS:
        total = Production(
            grain=total.grain,
            goods=total.goods + B.FIEF_BASE_GOODS,
            might=total.might,
            defense=total.defense,
        )
    if hungry:
        total = total.scale(B.HUNGER_PRODUCTION_MULT)
    return total


# Свободная клетка: слот метки той же ширины, что и буква владельца
MAP_EMPTY_MARK = "·"
# В Telegram <pre> эмодзи ≈ 2 колонки → клетка "метка+эмодзи" ≈ 3.
# Клетки стыкуем без пробелов: пробел после эмодзи часто теряет моноширинность.
_MAP_CELL_DISPLAY_WIDTH = 3


def owner_mark(index: int) -> str:
    # 0→А-подобные короткие метки: цифры и латиница для компактности легенды
    alphabet = "КМНОПРСТУФХАВЕ"
    if index < len(alphabet):
        return alphabet[index]
    return str(index % 10)


def format_map_cell(
    tile: TileView | None,
    marks: dict[int, str],
    *,
    claimable: set[tuple[int, int]] | None = None,
) -> str:
    """Клетка фиксированного вида: метка + эмодзи (сетка не плывёт)."""
    if not tile:
        return f"{MAP_EMPTY_MARK}❓"
    emoji = B.TILE_EMOJI.get(tile.tile_type, "❓")
    if tile.is_overgrown:
        emoji = "🌿"
    if tile.owner_fief_id is not None:
        mark = marks[tile.owner_fief_id]
    elif claimable and (tile.x, tile.y) in claimable:
        mark = "+"
    else:
        mark = MAP_EMPTY_MARK
    return f"{mark}{emoji}"


def map_owner_marks(tiles: list[TileView]) -> dict[int, str]:
    """Буквы владельцев: тот же порядок, что на PNG и в текстовой сетке."""
    fief_ids = sorted({t.owner_fief_id for t in tiles if t.owner_fief_id is not None})
    return {fid: owner_mark(i) for i, fid in enumerate(fief_ids)}


# В подписи к фото: вы всегда сверху; остальных не больше этого числа.
MAP_OWNER_CAPTION_MAX_OTHERS = 5


def format_map_you_pin(
    marks: dict[int, str],
    *,
    highlight_fief_id: int | None = None,
) -> str | None:
    """Короткая метка зрителя: 'вы = К'."""
    if highlight_fief_id is None:
        return None
    mark = marks.get(highlight_fief_id)
    if mark is None:
        return None
    return f"вы = {mark}"


def format_map_owners(
    legend: dict[int, str],
    marks: dict[int, str],
    *,
    highlight_fief_id: int | None = None,
    max_others: int = MAP_OWNER_CAPTION_MAX_OTHERS,
) -> str | None:
    """Список владельцев под легендой; себя не дублирует (есть вы = …)."""
    if not marks:
        return "Кто:\nна карте пока никого"

    others: list[str] = []
    for fid in sorted(marks):
        if highlight_fief_id is not None and fid == highlight_fief_id:
            continue
        name = str(legend.get(fid, f"#{fid}")).strip() or f"#{fid}"
        others.append(f"{marks[fid]} = {name}")

    if not others:
        # Только вы на карте - блока "Кто" не нужно.
        if highlight_fief_id is not None and highlight_fief_id in marks:
            return None
        return "Кто:\nна карте пока никого"

    shown = others[: max(0, max_others)]
    hidden = len(others) - len(shown)
    lines = ["Кто:", *shown]
    if hidden > 0:
        lines.append(f"ещё {hidden}")
    return "\n".join(lines)


def render_map_parts(
    width: int,
    height: int,
    tiles: list[TileView],
    legend: dict[int, str],
    *,
    highlight_fief_id: int | None = None,
    claimable: set[tuple[int, int]] | None = None,
) -> tuple[str, str]:
    """Сетка (для <pre>) и footer: вы → рамки/местность → кто."""
    by_pos = {(t.x, t.y): t for t in tiles}
    marks = map_owner_marks(tiles)

    # Буква над слотом метки; без межклеточных пробелов (см. _MAP_CELL_DISPLAY_WIDTH)
    header = "   " + "".join(
        f"{col_label(x):<{_MAP_CELL_DISPLAY_WIDTH}}" for x in range(width)
    )
    lines = [header]
    for y in range(height):
        cells = [
            format_map_cell(by_pos.get((x, y)), marks, claimable=claimable)
            for x in range(width)
        ]
        lines.append(f"{y + 1:>2} " + "".join(cells))

    from app.domain.guide import map_tile_legend

    footer_parts: list[str] = []
    you_pin = format_map_you_pin(marks, highlight_fief_id=highlight_fief_id)
    if you_pin:
        footer_parts.append(you_pin)
    footer_parts.append(map_tile_legend())
    owners = format_map_owners(
        legend, marks, highlight_fief_id=highlight_fief_id
    )
    if owners:
        footer_parts.append(owners)
    return "\n".join(lines), "\n\n".join(footer_parts)


def render_map(
    width: int,
    height: int,
    tiles: list[TileView],
    legend: dict[int, str],
    *,
    highlight_fief_id: int | None = None,
    claimable: set[tuple[int, int]] | None = None,
) -> str:
    grid, footer = render_map_parts(
        width,
        height,
        tiles,
        legend,
        highlight_fief_id=highlight_fief_id,
        claimable=claimable,
    )
    return f"{grid}\n\n{footer}" if footer else grid


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
