"""PNG-карта: рендер, отпечаток состояния и LRU-кэш."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image, ImageFont

from app import balance as B
from app.domain.economy import TileView
from app.domain.map_image import (
    MapImageCache,
    MapPhoto,
    _FONT_PATH,
    build_map_caption,
    map_fingerprint,
    render_map_image,
    require_map_font,
)
from app.engine import Engine
from app.handlers.shared import reply_map_photo
from app.messaging import answer_photo_bytes
from aiogram.exceptions import TelegramBadRequest


def _tile(
    x: int,
    y: int,
    tile_type: str,
    owner: int | None = None,
    *,
    building: str | None = None,
    building_level: int = 0,
    is_overgrown: bool = False,
) -> TileView:
    return TileView(
        x=x,
        y=y,
        tile_type=tile_type,
        owner_fief_id=owner,
        building=building,
        building_level=building_level,
        is_overgrown=is_overgrown,
    )


def test_bundled_map_font_supports_cyrillic():
    path = require_map_font()
    assert path == _FONT_PATH
    font = ImageFont.truetype(str(path), size=20)
    left, top, right, bottom = font.getbbox("Клетки АБВ")
    assert right - left > 20
    assert bottom - top > 8


def test_render_map_image_returns_valid_png():
    tiles = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_FOREST),
        _tile(0, 1, B.TILE_HILLS, owner=2),
        _tile(1, 1, B.TILE_RIVER, is_overgrown=True),
    ]
    png = render_map_image(2, 2, tiles, highlight_fief_id=1, claimable={(1, 0)})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    image = Image.open(__import__("io").BytesIO(png))
    assert image.format == "PNG"
    assert image.size[0] >= 2 * 56
    assert image.size[1] >= 2 * 56


def test_foreign_buildings_hidden_on_map_and_fingerprint():
    base = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_HILLS, owner=2),
    ]
    with_enemy = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_HILLS, owner=2, building=B.BLD_WATCH, building_level=2),
    ]
    common = dict(realm_id=1, width=2, height=1, highlight_fief_id=1, claimable=set())
    assert map_fingerprint(tiles=base, **common) == map_fingerprint(tiles=with_enemy, **common)
    assert render_map_image(2, 1, base, highlight_fief_id=1) == render_map_image(
        2, 1, with_enemy, highlight_fief_id=1
    )
    # свои постройки влияют на картинку
    upgraded = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=2),
        _tile(1, 0, B.TILE_HILLS, owner=2, building=B.BLD_WATCH, building_level=2),
    ]
    assert render_map_image(2, 1, with_enemy, highlight_fief_id=1) != render_map_image(
        2, 1, upgraded, highlight_fief_id=1
    )
    # без highlight чужие (и любые) постройки не рисуются
    assert render_map_image(2, 1, with_enemy, highlight_fief_id=None) == render_map_image(
        2, 1, base, highlight_fief_id=None
    )


def test_map_fingerprint_changes_on_off_tick_claim():
    base_tiles = [
        _tile(0, 0, B.TILE_FIELD, owner=1),
        _tile(1, 0, B.TILE_FOREST),
    ]
    common = dict(
        realm_id=1,
        width=2,
        height=1,
        highlight_fief_id=1,
    )
    before = map_fingerprint(tiles=base_tiles, claimable={(1, 0)}, **common)
    after_tiles = [
        _tile(0, 0, B.TILE_FIELD, owner=1),
        _tile(1, 0, B.TILE_FOREST, owner=1),
    ]
    after = map_fingerprint(tiles=after_tiles, claimable=set(), **common)
    assert before != after


def test_map_fingerprint_stable_for_caption_only_fields():
    """День/название/легенда не входят в ключ PNG - иначе тик сбрасывает file_id."""
    tiles = [_tile(0, 0, B.TILE_FIELD, owner=1)]
    fp = map_fingerprint(
        realm_id=1,
        width=1,
        height=1,
        tiles=tiles,
        highlight_fief_id=1,
        claimable=set(),
    )
    # повтор с теми же плитками - тот же ключ (подпись строится отдельно)
    again = map_fingerprint(
        realm_id=1,
        width=1,
        height=1,
        tiles=tiles,
        highlight_fief_id=1,
        claimable=set(),
    )
    assert fp == again


def test_map_fingerprint_differs_by_viewer_highlight():
    tiles = [_tile(0, 0, B.TILE_FIELD, owner=1), _tile(1, 0, B.TILE_FIELD, owner=2)]
    common = dict(
        realm_id=1,
        width=2,
        height=1,
        tiles=tiles,
        claimable=set(),
    )
    a = map_fingerprint(**common, highlight_fief_id=1)
    b = map_fingerprint(**common, highlight_fief_id=2)
    assert a != b


def test_map_image_cache_lru_and_file_id():
    cache = MapImageCache(max_entries=2)
    cache.put_png("a", b"png-a")
    cache.put_png("b", b"png-b")
    cache.set_file_id("a", "file-a")
    assert cache.get("a").file_id == "file-a"
    cache.put_png("c", b"png-c")
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert len(cache) == 2


def test_build_map_caption_splits_when_over_limit():
    footer = "x" * 1200
    caption, extra = build_map_caption(title="Долина", day_number=2, footer=footer, limit=1024)
    assert len(caption) <= 1024
    assert extra == footer
    short, none = build_map_caption(title="Долина", day_number=2, footer="Клетки:\nполе")
    assert none is None
    assert "Долина" in short
    assert "Клетки:" in short


def test_engine_map_photo_uses_cache_across_requests():
    db = MagicMock()
    db.get_realm.return_value = {
        "id": 1,
        "title": "Долина",
        "day_number": 3,
        "width": 2,
        "height": 1,
    }
    db.list_fiefs.return_value = [{"id": 1, "name": "Усадьба А", "pact_id": None, "user_id": 10}]
    db.get_pact.return_value = None
    engine = Engine(db)
    tiles = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_FOREST),
    ]
    engine.tile_views = MagicMock(return_value=tiles)  # type: ignore[method-assign]
    engine.fief_label = MagicMock(return_value="Усадьба А")  # type: ignore[method-assign]

    first = engine.map_photo(1, highlight_fief_id=1)
    second = engine.map_photo(1, highlight_fief_id=1)
    assert first.fingerprint == second.fingerprint
    assert first.png_bytes == second.png_bytes
    assert first.png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    assert "Долина" in first.caption
    assert "Клетки:" in first.caption

    engine.remember_map_file_id(first.fingerprint, "AgADBAAD")
    third = engine.map_photo(1, highlight_fief_id=1)
    assert third.file_id == "AgADBAAD"

    # день в подписи меняется, PNG-ключ тот же
    db.get_realm.return_value = {
        "id": 1,
        "title": "Долина",
        "day_number": 4,
        "width": 2,
        "height": 1,
    }
    day_bump = engine.map_photo(1, highlight_fief_id=1)
    assert day_bump.fingerprint == first.fingerprint
    assert day_bump.file_id == "AgADBAAD"
    assert "день 4" in day_bump.caption

    claimed = [
        _tile(0, 0, B.TILE_FIELD, owner=1, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_FOREST, owner=1),
    ]
    engine.tile_views = MagicMock(return_value=claimed)  # type: ignore[method-assign]
    after_claim = engine.map_photo(1, highlight_fief_id=1)
    assert after_claim.fingerprint != first.fingerprint
    assert after_claim.file_id is None


@pytest.mark.asyncio
async def test_answer_photo_bytes_reuploads_only_on_stale_file_id():
    message = MagicMock()
    good = MagicMock()
    message.answer_photo = AsyncMock(
        side_effect=[
            TelegramBadRequest(method=MagicMock(), message="Wrong file identifier"),
            good,
        ]
    )
    sent = await answer_photo_bytes(
        message, b"png", caption="cap", file_id="stale-id"
    )
    assert sent is good
    assert message.answer_photo.await_count == 2

    message.answer_photo = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="caption is too long")
    )
    sent2 = await answer_photo_bytes(
        message, b"png", caption="cap", file_id="maybe-ok"
    )
    assert sent2 is None
    assert message.answer_photo.await_count == 1


@pytest.mark.asyncio
async def test_reply_map_photo_remembers_file_id_and_skips_extra_on_fail():
    engine = MagicMock()
    photo = MapPhoto(
        png_bytes=b"\x89PNG\r\n\x1a\n",
        caption="cap",
        fingerprint="fp1",
        file_id=None,
        caption_extra="extra legend",
    )
    message = MagicMock()
    sent = MagicMock()
    sent.photo = [MagicMock(file_id="small"), MagicMock(file_id="large-id")]

    with patch(
        "app.handlers.shared.answer_photo_bytes", new=AsyncMock(return_value=sent)
    ) as send_mock, patch(
        "app.handlers.shared.reply_game", new=AsyncMock()
    ) as reply_mock, patch(
        "app.handlers.shared.answer_html", new=AsyncMock()
    ) as html_mock:
        await reply_map_photo(message, engine, photo)
        send_mock.assert_awaited_once()
        engine.remember_map_file_id.assert_called_once_with("fp1", "large-id")
        reply_mock.assert_awaited_once_with(message, "extra legend")
        html_mock.assert_not_awaited()

    with patch(
        "app.handlers.shared.answer_photo_bytes", new=AsyncMock(return_value=None)
    ), patch(
        "app.handlers.shared.reply_game", new=AsyncMock()
    ) as reply_mock, patch(
        "app.handlers.shared.answer_html", new=AsyncMock()
    ) as html_mock:
        await reply_map_photo(message, engine, photo)
        reply_mock.assert_not_awaited()
        html_mock.assert_awaited_once()
