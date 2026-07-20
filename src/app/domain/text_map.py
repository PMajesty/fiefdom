"""Эмодзи-карта долины: метки владельцев, сетка, легенда."""
from __future__ import annotations

from app import balance as B
from app.domain.map_gen import col_label
from app.domain.production import TileView

# Свободная клетка: слот метки той же ширины, что и буква владельца
MAP_EMPTY_MARK = "·"
# В Telegram <pre> эмодзи ≈ 2 колонки → клетка "метка+эмодзи" ≈ 3.
# Клетки стыкуем без пробелов: пробел после эмодзи часто теряет моноширинность.
_MAP_CELL_DISPLAY_WIDTH = 3

# В подписи к фото: вы всегда сверху; остальных не больше этого числа.
MAP_OWNER_CAPTION_MAX_OTHERS = 5


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
