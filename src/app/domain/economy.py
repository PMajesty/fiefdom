"""Рендер эмодзи-карты и расчёт дневного производства."""
from __future__ import annotations

from dataclasses import dataclass

from app import balance as B
from app.domain.map_gen import col_label, coord_label


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
) -> Production:
    total = Production()
    for t in tiles:
        if t.is_overgrown:
            continue
        p = tile_passive(t.tile_type)
        b = building_production(t.building or "", t.building_level, t.tile_type)
        if t.building == B.BLD_FARM:
            b = Production(grain=b.grain * farm_mult, goods=b.goods, might=b.might, defense=b.defense)
        total = Production(
            grain=total.grain + p.grain + b.grain,
            goods=total.goods + p.goods + b.goods,
            might=total.might + p.might + b.might,
            defense=total.defense + b.defense,
        )
    if hungry:
        total = total.scale(B.HUNGER_PRODUCTION_MULT)
    return total


def owner_mark(index: int) -> str:
    # 0→А-подобные короткие метки: цифры и латиница для компактности легенды
    alphabet = "КМНОПРСТУФХАВЕ"
    if index < len(alphabet):
        return alphabet[index]
    return str(index % 10)


def render_map(
    width: int,
    height: int,
    tiles: list[TileView],
    legend: dict[int, str],
    *,
    highlight_fief_id: int | None = None,
    claimable: set[tuple[int, int]] | None = None,
) -> str:
    by_pos = {(t.x, t.y): t for t in tiles}
    fief_ids = sorted({t.owner_fief_id for t in tiles if t.owner_fief_id is not None})
    marks = {fid: owner_mark(i) for i, fid in enumerate(fief_ids)}

    header = "   " + "".join(f"{col_label(x):>2}" for x in range(width))
    lines = [header]
    for y in range(height):
        row = [f"{y + 1:>2} "]
        for x in range(width):
            t = by_pos.get((x, y))
            if not t:
                row.append("❓")
                continue
            emoji = B.TILE_EMOJI.get(t.tile_type, "❓")
            if t.is_overgrown:
                emoji = "🌿"
            cell = emoji
            if t.owner_fief_id is not None:
                cell = f"{emoji}{marks[t.owner_fief_id]}"
            if highlight_fief_id is not None and t.owner_fief_id == highlight_fief_id:
                cell = f"[{cell}]"
            elif claimable and (x, y) in claimable and t.owner_fief_id is None:
                cell = f"+{emoji}"
            row.append(cell)
        lines.append("".join(row))

    leg_lines = []
    for fid in fief_ids:
        name = legend.get(fid, f"#{fid}")
        leg_lines.append(f"{marks[fid]} = {name}")
    body = "\n".join(lines)
    if leg_lines:
        body += "\n\n" + "\n".join(leg_lines)
    return body


def adjacent_claimable(
    owned: set[tuple[int, int]],
    all_tiles: dict[tuple[int, int], TileView],
    *,
    for_fief_id: int | None = None,
) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for x, y in owned:
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            p = (x + dx, y + dy)
            t = all_tiles.get(p)
            if not t:
                continue
            # свободно или заросшее чужое
            if t.owner_fief_id is None:
                out.add(p)
            elif t.is_overgrown and t.owner_fief_id != for_fief_id:
                out.add(p)
    return out
