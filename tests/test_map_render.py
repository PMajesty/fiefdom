"""Рендер карты: фиксированная ширина клеток и метки владельцев."""
from __future__ import annotations

from app import balance as B
from app.domain.economy import (
    MAP_EMPTY_MARK,
    TileView,
    adjacent_claimable,
    format_map_cell,
    pick_max_separated_tiles,
    render_map,
    render_map_parts,
    too_close_to_ruins,
    toroidal_manhattan,
)


def _tile(x: int, y: int, tile_type: str, owner: int | None = None) -> TileView:
    return TileView(
        x=x,
        y=y,
        tile_type=tile_type,
        owner_fief_id=owner,
        building=None,
        building_level=0,
    )


def test_format_map_cell_keeps_mark_slot():
    marks = {1: "К"}
    empty = _tile(0, 0, B.TILE_FIELD)
    owned = _tile(1, 0, B.TILE_FIELD, owner=1)
    claimable = {(0, 0)}

    assert format_map_cell(empty, marks) == f"{MAP_EMPTY_MARK}{B.TILE_EMOJI[B.TILE_FIELD]}"
    assert format_map_cell(owned, marks) == f"К{B.TILE_EMOJI[B.TILE_FIELD]}"
    assert format_map_cell(empty, marks, claimable=claimable) == f"+{B.TILE_EMOJI[B.TILE_FIELD]}"
    assert len(format_map_cell(empty, marks)) == len(format_map_cell(owned, marks))


def test_render_map_aligned_columns_with_owners():
    tiles = [
        _tile(0, 0, B.TILE_FIELD, owner=1),
        _tile(1, 0, B.TILE_FOREST),
        _tile(0, 1, B.TILE_FIELD),
        _tile(1, 1, B.TILE_FIELD, owner=2),
    ]
    body = render_map(
        2,
        2,
        tiles,
        {1: "Усадьба А", 2: "Усадьба Б"},
        highlight_fief_id=1,
        claimable={(1, 0)},
    )
    grid_lines = body.split("\n\n")[0].split("\n")
    # без пробелов между клетками: буква + pad до ширины клетки (метка+эмодзи≈3)
    assert grid_lines[0] == "   А  Б  "
    row1 = grid_lines[1]
    row2 = grid_lines[2]
    assert row1.startswith(" 1 ")
    assert row2.startswith(" 2 ")
    # клетки стыкованы без пробелов - стабильнее в Telegram после эмодзи
    assert " " not in row1[3:]
    assert " " not in row2[3:]
    assert row1[3:].startswith("К")
    assert f"+{B.TILE_EMOJI[B.TILE_FOREST]}" in row1
    assert row2[3:].startswith(MAP_EMPTY_MARK)
    assert "М" in row2
    assert "[" not in body
    assert "К = Усадьба А (это вы)" in body
    assert "можно занять" in body
    assert "Рамки:" in body
    assert "только свои постройки" in body
    assert "Местность" in body
    assert f"{B.TILE_EMOJI[B.TILE_FIELD]} поле" in body


def test_render_map_parts_separates_grid_and_footer():
    tiles = [_tile(0, 0, B.TILE_FIELD, owner=1)]
    grid, footer = render_map_parts(1, 1, tiles, {1: "Усадьба А"}, highlight_fief_id=1)
    assert "<" not in grid
    assert grid.startswith("   А  ")
    assert "\n 1 " in grid
    assert "Владельцы:" in footer
    assert "можно занять" in footer
    assert "К = Усадьба А (это вы)" in footer
    assert "Владельцы:" not in grid


def test_map_tile_legend_reads_naturally():
    from app.domain.guide import map_tile_legend

    text = map_tile_legend()
    assert "Местность (иконка на клетке):" in text
    assert f"{B.TILE_EMOJI[B.TILE_FIELD]} поле" in text
    assert f"{B.TILE_EMOJI[B.TILE_FOREST]} лес" in text
    assert f"{B.TILE_EMOJI[B.TILE_HILLS]} холмы" in text
    assert f"{B.TILE_EMOJI[B.TILE_RIVER]} река" in text
    assert f"{B.TILE_EMOJI[B.TILE_ROAD]} дорога" in text
    assert f"{B.TILE_EMOJI[B.TILE_RUINS]} руины" in text
    assert f"{B.TILE_EMOJI[B.TILE_WILDS]} глушь" in text
    assert "Рамки:" in text
    assert "синяя - ваши" in text
    assert "жёлтая - можно занять" in text
    assert "Буква на клетке" in text
    assert "Д двор" in text
    assert "Ф ферма" in text
    assert "· свободно" not in text
    assert "+ можно" not in text
    assert "← вы" not in text
    assert "ферма ×" not in text


def test_stash_status_line_copy():
    from app.engine import _stash_status_line

    assert _stash_status_line(0) == f"Склад до {B.DEFAULT_STASH_CAP} · без амбара"
    assert _stash_status_line(2) == f"Склад до {B.stash_cap(2)} · амбар II"


def test_map_text_wraps_only_grid_in_pre():
    from unittest.mock import MagicMock

    from app.engine import Engine

    db = MagicMock()
    db.get_realm.return_value = {
        "id": 1,
        "title": "Долина",
        "day_number": 3,
        "width": 2,
        "height": 1,
    }
    db.list_fiefs.return_value = [{"id": 1, "name": "Усадьба А", "pact_id": None, "user_id": 10}]
    engine = Engine(db)
    engine.tile_views = MagicMock(  # type: ignore[method-assign]
        return_value=[_tile(0, 0, B.TILE_FIELD, owner=1), _tile(1, 0, B.TILE_FOREST)]
    )
    engine.fief_label = MagicMock(return_value="Усадьба А")  # type: ignore[method-assign]

    text = engine.map_text(1, highlight_fief_id=1)
    assert text.startswith("🗺️ Долина (день 3)\n<pre>")
    assert "</pre>\n\nВладельцы:" in text
    assert "Рамки:" in text
    assert "Местность" in text
    pre = text.split("<pre>", 1)[1].split("</pre>", 1)[0]
    assert "Владельцы:" not in pre
    assert "Рамки:" not in pre
    assert "Местность" not in pre


def test_toroidal_manhattan_wraps():
    assert toroidal_manhattan(0, 0, 5, 0, 6, 6) == 1
    assert toroidal_manhattan(0, 0, 0, 5, 6, 6) == 1
    assert toroidal_manhattan(0, 0, 5, 5, 6, 6) == 2
    assert toroidal_manhattan(0, 0, 3, 3, 6, 6) == 6


def test_adjacent_claimable_wraps_edges():
    w, h = 6, 6
    tiles = {
        (x, y): _tile(x, y, B.TILE_FIELD, owner=(1 if (x, y) == (0, 0) else None))
        for x in range(w)
        for y in range(h)
    }
    claimable = adjacent_claimable({(0, 0)}, tiles, width=w, height=h, for_fief_id=1)
    assert (1, 0) in claimable
    assert (0, 1) in claimable
    assert (5, 0) in claimable  # wrap left→right
    assert (0, 5) in claimable  # wrap top→bottom
    assert (2, 2) not in claimable


def test_too_close_to_ruins_blocks_self_and_orthogonal():
    ruins = [(2, 2)]
    w = h = 6
    assert too_close_to_ruins(2, 2, ruins, w, h) is True
    assert too_close_to_ruins(1, 2, ruins, w, h) is True
    assert too_close_to_ruins(2, 1, ruins, w, h) is True
    assert too_close_to_ruins(1, 1, ruins, w, h) is False  # диагональ, dist=2
    assert too_close_to_ruins(0, 0, ruins, w, h) is False


def test_too_close_to_ruins_wraps_toroidally():
    ruins = [(0, 0)]
    w = h = 6
    assert too_close_to_ruins(5, 0, ruins, w, h) is True
    assert too_close_to_ruins(0, 5, ruins, w, h) is True
    assert too_close_to_ruins(5, 5, ruins, w, h) is False


def test_pick_max_separated_from_existing_core():
    w, h = 6, 6
    candidates = [
        {"id": i, "x": x, "y": y}
        for i, (x, y) in enumerate((x, y) for x in range(w) for y in range(h))
        if (x, y) != (0, 0)
    ]
    picked = pick_max_separated_tiles(candidates, anchors=[(0, 0)], width=w, height=h, count=1)
    assert len(picked) == 1
    assert toroidal_manhattan(picked[0]["x"], picked[0]["y"], 0, 0, w, h) == 6


def test_pick_max_separated_spreads_choices():
    w, h = 6, 6
    candidates = [
        {"id": i, "x": x, "y": y}
        for i, (x, y) in enumerate((x, y) for x in range(w) for y in range(h))
    ]
    picked = pick_max_separated_tiles(candidates, anchors=[], width=w, height=h, count=4)
    assert len(picked) == 4
    coords = [(p["x"], p["y"]) for p in picked]
    for i, (x1, y1) in enumerate(coords):
        for x2, y2 in coords[i + 1 :]:
            assert toroidal_manhattan(x1, y1, x2, y2, w, h) >= 2


def test_build_action_cost_helpers():
    empty = {"building": None, "building_level": 0, "damaged": False}
    farm1 = {"building": B.BLD_FARM, "building_level": 1, "damaged": False}
    assert B.build_action_cost(B.BLD_FARM, empty) == 20
    assert B.build_action_cost(B.BLD_FARM, farm1) == 50
    assert B.build_action_cost(B.BLD_FARM, farm1, cost_mult=0.75) == 37
    assert B.cheapest_build_action_cost(B.BLD_FARM, [farm1, empty]) == 20
    assert B.min_any_build_action_cost([farm1]) == 50
