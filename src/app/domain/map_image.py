"""Растровая карта долины (PNG) и кэш по отпечатку состояния."""
from __future__ import annotations

import hashlib
import io
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app import balance as B
from app.domain.economy import MAP_EMPTY_MARK, TileView, owner_mark
from app.domain.map_gen import col_label

CELL_PX = 56
LABEL_LEFT_PX = 36
LABEL_TOP_PX = 28
PAD_PX = 16
GRID_LINE_PX = 2
# Меняйте при правках вида клетки - сброс кэша PNG/file_id.
RENDER_REV = 2

_FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSans-Regular.ttf"

TILE_COLORS: dict[str, tuple[int, int, int]] = {
    B.TILE_FIELD: (196, 178, 98),
    B.TILE_FOREST: (61, 107, 79),
    B.TILE_HILLS: (139, 115, 85),
    B.TILE_RIVER: (74, 144, 164),
    B.TILE_ROAD: (184, 160, 112),
    B.TILE_RUINS: (107, 91, 91),
    B.TILE_WILDS: (90, 107, 74),
}

BUILDING_MARK: dict[str, str] = {
    B.BLD_MANOR: "Д",
    B.BLD_FARM: "Ф",
    B.BLD_WORKSHOP: "Р",
    B.BLD_WATCH: "С",
    B.BLD_BARN: "А",
}

BG_COLOR = (232, 220, 200)
GRID_COLOR = (58, 47, 36)
LABEL_COLOR = (42, 34, 28)
OWNER_TEXT = (28, 22, 18)
CLAIM_BORDER = (214, 160, 40)
HIGHLIGHT_BORDER = (40, 90, 160)
OVERGROWN_TINT = (80, 140, 70, 110)


@dataclass(frozen=True)
class MapPhoto:
    png_bytes: bytes
    caption: str
    fingerprint: str
    file_id: str | None = None
    caption_extra: str | None = None


@dataclass
class _CacheEntry:
    png_bytes: bytes
    file_id: str | None = None


class MapImageCache:
    """LRU: отпечаток состояния → PNG и опциональный Telegram file_id."""

    def __init__(self, max_entries: int = 96) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, fingerprint: str) -> _CacheEntry | None:
        entry = self._entries.get(fingerprint)
        if entry is None:
            return None
        self._entries.move_to_end(fingerprint)
        return entry

    def put_png(self, fingerprint: str, png_bytes: bytes) -> _CacheEntry:
        existing = self._entries.get(fingerprint)
        if existing is not None:
            existing.png_bytes = png_bytes
            self._entries.move_to_end(fingerprint)
            return existing
        entry = _CacheEntry(png_bytes=png_bytes)
        self._entries[fingerprint] = entry
        self._entries.move_to_end(fingerprint)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)
        return entry

    def set_file_id(self, fingerprint: str, file_id: str) -> None:
        entry = self._entries.get(fingerprint)
        if entry is None:
            return
        entry.file_id = file_id
        self._entries.move_to_end(fingerprint)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    path = require_map_font()
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise OSError(f"Не удалось открыть шрифт карты: {path}") from exc


def building_visible_on_map(
    tile: TileView, highlight_fief_id: int | None
) -> bool:
    """Чужие постройки скрыты: их узнают через слухи и общение."""
    if highlight_fief_id is None:
        return False
    if tile.owner_fief_id != highlight_fief_id:
        return False
    return bool(tile.building) and int(tile.building_level or 0) > 0


def map_fingerprint(
    *,
    realm_id: int,
    width: int,
    height: int,
    tiles: list[TileView],
    highlight_fief_id: int | None,
    claimable: set[tuple[int, int]] | None,
) -> str:
    """Отпечаток только того, что влияет на PNG (не подпись)."""
    tile_rows = []
    for t in sorted(tiles, key=lambda item: (item.y, item.x)):
        show_bld = building_visible_on_map(t, highlight_fief_id)
        tile_rows.append(
            [
                t.x,
                t.y,
                t.tile_type,
                t.owner_fief_id,
                t.building if show_bld else None,
                t.building_level if show_bld else 0,
                int(t.is_bridge),
                int(t.is_core),
                int(t.is_overgrown),
            ]
        )
    claim_rows = [[x, y] for x, y in sorted(claimable)] if claimable else []
    payload = {
        "realm_id": realm_id,
        "width": width,
        "height": height,
        "tiles": tile_rows,
        "highlight_fief_id": highlight_fief_id,
        "claimable": claim_rows,
        "cell_px": CELL_PX,
        "render_rev": RENDER_REV,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def require_map_font() -> Path:
    """Путь к TTF с кириллицей; без файла карта нечитаема."""
    if not _FONT_PATH.is_file():
        raise FileNotFoundError(f"Нет шрифта карты: {_FONT_PATH}")
    return _FONT_PATH


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor="mm")


def render_map_image(
    width: int,
    height: int,
    tiles: list[TileView],
    *,
    highlight_fief_id: int | None = None,
    claimable: set[tuple[int, int]] | None = None,
) -> bytes:
    """Собирает PNG сетки: цвет местности, метка владельца, здание, + клейма."""
    by_pos = {(t.x, t.y): t for t in tiles}
    fief_ids = sorted({t.owner_fief_id for t in tiles if t.owner_fief_id is not None})
    marks = {fid: owner_mark(i) for i, fid in enumerate(fief_ids)}

    img_w = LABEL_LEFT_PX + width * CELL_PX + PAD_PX
    img_h = LABEL_TOP_PX + height * CELL_PX + PAD_PX
    image = Image.new("RGB", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(image)
    font_label = _load_font(16)
    font_owner = _load_font(22)
    font_building = _load_font(14)

    for x in range(width):
        _draw_centered_text(
            draw,
            (LABEL_LEFT_PX + x * CELL_PX + CELL_PX / 2, LABEL_TOP_PX / 2),
            col_label(x),
            font_label,
            LABEL_COLOR,
        )
    for y in range(height):
        _draw_centered_text(
            draw,
            (LABEL_LEFT_PX / 2, LABEL_TOP_PX + y * CELL_PX + CELL_PX / 2),
            str(y + 1),
            font_label,
            LABEL_COLOR,
        )

    for y in range(height):
        for x in range(width):
            left = LABEL_LEFT_PX + x * CELL_PX
            top = LABEL_TOP_PX + y * CELL_PX
            right = left + CELL_PX
            bottom = top + CELL_PX
            tile = by_pos.get((x, y))
            fill = TILE_COLORS.get(tile.tile_type if tile else "", (160, 160, 160))
            draw.rectangle((left, top, right, bottom), fill=fill)

            if tile and tile.is_overgrown:
                overlay = Image.new("RGBA", (CELL_PX, CELL_PX), OVERGROWN_TINT)
                hatch = ImageDraw.Draw(overlay)
                for step in range(-CELL_PX, CELL_PX, 8):
                    hatch.line((step, 0, step + CELL_PX, CELL_PX), fill=(40, 80, 40, 160), width=2)
                image.paste(overlay, (left, top), overlay)

            border = GRID_COLOR
            border_w = GRID_LINE_PX
            if claimable and (x, y) in claimable:
                border = CLAIM_BORDER
                border_w = 3
            elif (
                tile
                and highlight_fief_id is not None
                and tile.owner_fief_id == highlight_fief_id
                and not tile.is_overgrown
            ):
                border = HIGHLIGHT_BORDER
                border_w = 3
            draw.rectangle((left, top, right - 1, bottom - 1), outline=border, width=border_w)

            if not tile:
                continue
            if tile.owner_fief_id is not None:
                mark = marks[tile.owner_fief_id]
            elif claimable and (x, y) in claimable:
                mark = "+"
            else:
                mark = MAP_EMPTY_MARK
            cx = left + CELL_PX / 2
            cy = top + CELL_PX / 2
            _draw_centered_text(draw, (cx, cy), mark, font_owner, OWNER_TEXT)
            if building_visible_on_map(tile, highlight_fief_id):
                bmark = BUILDING_MARK.get(tile.building or "", "?")
                label = f"{bmark}{tile.building_level}"
                draw.text(
                    (right - 5, bottom - 4),
                    label,
                    font=font_building,
                    fill=OWNER_TEXT,
                    anchor="rb",
                )

    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build_map_caption(
    *,
    title: str,
    day_number: int,
    footer: str,
    limit: int = 1024,
) -> tuple[str, str | None]:
    """Подпись к фото; хвост легенды отдельно, если не влезает в лимит Telegram."""
    header = f"🗺️ {title} (день {day_number})"
    if not footer:
        return header[:limit], None
    full = f"{header}\n\n{footer}"
    if len(full) <= limit:
        return full, None
    short = f"{header}\n\nЛегенда и владельцы - следующим сообщением."
    return short[:limit], footer
