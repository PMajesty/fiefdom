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
RENDER_REV = 4

_FONT_PATH = Path(__file__).resolve().parents[1] / "assets" / "fonts" / "NotoSans-Regular.ttf"

TILE_COLORS: dict[str, tuple[int, int, int]] = {
    B.TILE_FIELD: (210, 188, 110),
    B.TILE_FOREST: (72, 118, 88),
    B.TILE_HILLS: (156, 128, 96),
    B.TILE_RIVER: (86, 152, 172),
    B.TILE_ROAD: (196, 172, 124),
    B.TILE_RUINS: (120, 104, 104),
    B.TILE_WILDS: (98, 114, 82),
}

# Пиктограммы - заметно темнее заливки, чтобы читались на телефоне.
TILE_MOTIF: dict[str, tuple[int, int, int]] = {
    B.TILE_FIELD: (120, 88, 28),
    B.TILE_FOREST: (28, 58, 36),
    B.TILE_HILLS: (86, 60, 36),
    B.TILE_RIVER: (28, 78, 104),
    B.TILE_ROAD: (96, 72, 40),
    B.TILE_RUINS: (52, 40, 40),
    B.TILE_WILDS: (44, 56, 32),
}
TILE_MOTIF_FILL: dict[str, tuple[int, int, int]] = {
    B.TILE_FOREST: (48, 92, 60),
    B.TILE_RUINS: (88, 72, 72),
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
    # Явный bbox: у кириллицы side-bearing часто съезжает при anchor="mm".
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = xy[0] - tw / 2 - bbox[0]
    y = xy[1] - th / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _draw_terrain_motif(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    tile_type: str,
) -> None:
    """Простые пиктограммы местности - узнаются без текстовой легенды."""
    ink = TILE_MOTIF.get(tile_type, (80, 80, 80))
    fill = TILE_MOTIF_FILL.get(tile_type)
    x0, y0 = left + 4, top + 4
    x1, y1 = left + CELL_PX - 5, top + CELL_PX - 5
    cx = left + CELL_PX // 2
    cy = top + CELL_PX // 2

    if tile_type == B.TILE_FIELD:
        for i, dx in enumerate((10, 20, 30, 40)):
            bx = left + dx
            head = y1 - 20 - (i % 2) * 3
            draw.line((bx, y1 - 3, bx, head + 4), fill=ink, width=2)
            draw.ellipse((bx - 4, head - 2, bx + 4, head + 6), outline=ink, width=2)
    elif tile_type == B.TILE_FOREST:
        for ox, oy in ((14, 20), (36, 16), (26, 34)):
            tip = (left + ox, top + oy - 14)
            base_l = (left + ox - 9, top + oy + 2)
            base_r = (left + ox + 9, top + oy + 2)
            draw.polygon([tip, base_l, base_r], fill=fill or ink, outline=ink)
            draw.line((left + ox, top + oy + 2, left + ox, top + oy + 9), fill=ink, width=2)
    elif tile_type == B.TILE_HILLS:
        draw.arc((x0, cy - 8, cx + 6, y1), start=200, end=340, fill=ink, width=3)
        draw.arc((cx - 10, cy - 2, x1, y1 + 2), start=200, end=340, fill=ink, width=3)
    elif tile_type == B.TILE_RIVER:
        for yy in (top + 14, top + 28, top + 42):
            pts = [
                (x0, yy),
                (x0 + 12, yy - 5),
                (x0 + 24, yy + 3),
                (x0 + 36, yy - 4),
                (x1, yy + 2),
            ]
            draw.line(pts, fill=ink, width=3)
    elif tile_type == B.TILE_ROAD:
        draw.line((x0 + 2, y1 - 6, x1 - 2, y0 + 8), fill=ink, width=5)
        for t in (0.18, 0.4, 0.62, 0.84):
            px = x0 + 2 + (x1 - 4 - x0) * t
            py = y1 - 6 + (y0 + 8 - (y1 - 6)) * t
            draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=(232, 220, 200))
    elif tile_type == B.TILE_RUINS:
        draw.rectangle((x0 + 2, y0 + 12, x0 + 20, y1 - 4), fill=fill, outline=ink, width=2)
        draw.line((x0 + 2, y0 + 22, x0 + 20, y0 + 22), fill=ink, width=2)
        draw.rectangle((cx, cy - 2, x1 - 2, y1 - 6), outline=ink, width=2)
        draw.line((cx + 6, cy - 2, cx + 14, y0 + 4), fill=ink, width=3)
    elif tile_type == B.TILE_WILDS:
        for ox, oy in ((10, 12), (30, 10), (18, 26), (36, 28), (14, 38), (28, 40)):
            draw.ellipse((left + ox, top + oy, left + ox + 7, top + oy + 5), outline=ink, width=2)
        draw.arc((x0, y0 + 4, x1, cy + 6), start=10, end=170, fill=ink, width=2)
    else:
        draw.line((x0, y0, x1, y1), fill=ink, width=1)


def _draw_mark_badge(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    text: str,
    font: ImageFont.ImageFont,
) -> None:
    """Светлый кружок под меткой, чтобы буква читалась поверх пиктограмм."""
    draw.ellipse(
        (cx - 11, cy - 11, cx + 11, cy + 11),
        fill=(245, 236, 214),
        outline=GRID_COLOR,
        width=1,
    )
    _draw_centered_text(draw, (cx, cy), text, font, OWNER_TEXT)


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
    font_owner = _load_font(20)
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
            tile_type = tile.tile_type if tile else ""
            fill = TILE_COLORS.get(tile_type, (160, 160, 160))
            draw.rectangle((left, top, right, bottom), fill=fill)
            if tile_type:
                _draw_terrain_motif(draw, left, top, tile_type)

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
            _draw_mark_badge(draw, cx, cy, mark, font_owner)
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
