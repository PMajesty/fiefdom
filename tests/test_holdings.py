"""Обзор владений: клетки, здания и эффекты."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.economy import Production
from app.domain.holdings import (
    building_level_roman,
    format_holdings,
    tile_effect_text,
    tile_headline,
)
from app.engine import Engine
from app.handlers import callbacks as cb_mod


def _tile(
    x: int,
    y: int,
    tile_type: str,
    *,
    building: str | None = None,
    building_level: int = 0,
    damaged: bool = False,
    is_overgrown: bool = False,
) -> dict:
    return {
        "x": x,
        "y": y,
        "tile_type": tile_type,
        "building": building,
        "building_level": building_level,
        "damaged": damaged,
        "is_overgrown": is_overgrown,
        "owner_fief_id": 1,
    }


def test_building_level_roman():
    assert building_level_roman(1) == "I"
    assert building_level_roman(2) == "II"
    assert building_level_roman(3) == "III"
    assert building_level_roman(4) == "4"


def test_tile_headline_empty_and_built():
    empty = _tile(0, 0, B.TILE_FIELD)
    assert tile_headline(empty) == "А1 Поле · пусто"

    farm = _tile(1, 0, B.TILE_FIELD, building=B.BLD_FARM, building_level=2)
    assert tile_headline(farm) == "Б1 Поле · Ферма II"

    broken = _tile(
        2,
        0,
        B.TILE_HILLS,
        building=B.BLD_WATCH,
        building_level=1,
        damaged=True,
    )
    assert "Сторожка I" in tile_headline(broken)
    assert "повреждено" in tile_headline(broken)

    over = _tile(
        3,
        0,
        B.TILE_FOREST,
        building=B.BLD_WORKSHOP,
        building_level=1,
        is_overgrown=True,
    )
    assert "заросло" in tile_headline(over)


def test_tile_effect_farm_native_bonus():
    farm = _tile(0, 0, B.TILE_FIELD, building=B.BLD_FARM, building_level=1)
    text = tile_effect_text(farm)
    expected = int(B.FARM_YIELD[1] * B.NATIVE_BONUS)
    assert f"+{expected} зерна/день" == text


def test_tile_effect_watch_on_hills():
    watch = _tile(0, 0, B.TILE_HILLS, building=B.BLD_WATCH, building_level=1)
    text = tile_effect_text(watch)
    might = int(B.WATCH_MIGHT[1] * B.NATIVE_BONUS)
    defense = int(B.WATCH_DEFENSE[1] * B.NATIVE_BONUS)
    assert f"+{might} силы/день" in text
    assert f"+{defense} защиты" in text
    assert text == f"+{might} силы/день, +{defense} защиты"


def test_tile_effect_barn():
    barn = _tile(0, 0, B.TILE_FIELD, building=B.BLD_BARN, building_level=2)
    text = tile_effect_text(barn)
    assert str(B.stash_cap(2)) in text
    assert f"{int(round(B.barn_protect_frac(2) * 100))}%" in text
    assert str(B.collect_cap_days(2)) in text


def test_tile_effect_barn_keeps_river_passive():
    barn = _tile(0, 0, B.TILE_RIVER, building=B.BLD_BARN, building_level=1)
    text = tile_effect_text(barn)
    assert "склад до" in text
    assert f"+{B.RIVER_PASSIVE_GRAIN} зерна/день" in text


def test_tile_effect_river_empty():
    river = _tile(0, 0, B.TILE_RIVER)
    assert tile_effect_text(river) == f"+{B.RIVER_PASSIVE_GRAIN} зерна/день"


def test_tile_effect_overgrown_and_hungry():
    farm = _tile(
        0,
        0,
        B.TILE_FIELD,
        building=B.BLD_FARM,
        building_level=1,
        is_overgrown=True,
    )
    assert "заросло" in tile_effect_text(farm)

    active = _tile(0, 0, B.TILE_FIELD, building=B.BLD_FARM, building_level=1)
    hungry = tile_effect_text(active, hungry=True)
    full = int(B.FARM_YIELD[1] * B.NATIVE_BONUS)
    halved = int(full * B.HUNGER_PRODUCTION_MULT)
    assert f"+{halved} зерна/день" == hungry


def test_tile_effect_manor_notes_militia_cap():
    manor = _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1)
    text = tile_effect_text(manor, current_might=0)
    assert f"+{B.MANOR_GRAIN} зерна/день" in text
    assert f"+{B.MANOR_GOODS} товаров/день" in text
    assert f"+{B.MANOR_MIGHT} силы/день" in text
    assert f"пока дружина ниже {B.MILITIA_FREE}" in text


def test_tile_effect_manor_hides_might_at_free_cap():
    manor = _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1)
    text = tile_effect_text(manor, current_might=B.MILITIA_FREE)
    assert f"+{B.MANOR_GRAIN} зерна/день" in text
    assert f"+{B.MANOR_GOODS} товаров/день" in text
    assert "силы/день" not in text
    assert "сила двора не копится" in text
    assert f"потолка ({B.MILITIA_FREE})" in text


def test_tile_effect_manor_partial_free_room():
    manor = _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1)
    text = tile_effect_text(manor, current_might=B.MILITIA_FREE - 1)
    assert "+1 силы/день" in text
    assert "урезана до потолка" in text


def test_format_holdings_lists_tiles_help_and_totals():
    tiles = [
        _tile(1, 0, B.TILE_FOREST, building=B.BLD_WORKSHOP, building_level=1),
        _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1),
    ]
    daily = Production(grain=10, goods=15, might=2, defense=0)
    text = format_holdings(
        tiles,
        fief_label="Усадьба @test",
        daily=daily,
        current_might=0,
    )
    assert "Владения" in text
    assert "Усадьба @test" in text
    assert f"2/{B.TILE_HARD_CAP}" in text
    # Сортировка по y,x → А1 затем Б1
    assert text.index("А1 Поле · Двор I") < text.index("Б1 Лес · Мастерская I")
    assert "Справка по зданиям:" in text
    assert "Ферма - зерно" in text
    assert "Амбар - склад" in text
    assert "Итого: +10 зерна/день, +15 товаров/день, +2 силы/день" in text


def test_format_holdings_manor_matches_total_at_cap():
    tiles = [
        _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1),
        _tile(1, 0, B.TILE_HILLS, building=B.BLD_WATCH, building_level=1),
    ]
    watch_might = B.WATCH_MIGHT[1] * B.NATIVE_BONUS
    daily = Production(
        grain=B.MANOR_GRAIN,
        goods=B.MANOR_GOODS + B.FIEF_BASE_GOODS,
        might=watch_might,
        defense=B.WATCH_DEFENSE[1] * B.NATIVE_BONUS,
    )
    text = format_holdings(
        tiles,
        fief_label="Усадьба @test",
        daily=daily,
        current_might=B.MILITIA_FREE,
    )
    assert "сила двора не копится" in text
    assert f"+{watch_might:.0f} силы/день" in text
    assert f"+{daily.might:.0f} силы/день" in text.split("Итого:")[1]


def test_format_holdings_empty_and_hungry_banner():
    empty = format_holdings([], fief_label="Усадьба X")
    assert "Клеток пока нет." in empty

    hungry = format_holdings(
        [_tile(0, 0, B.TILE_FIELD)],
        fief_label="Усадьба X",
        hungry=True,
    )
    assert "Голод" in hungry


def test_engine_holdings_text_uses_fief_tiles():
    tiles = [
        _tile(0, 0, B.TILE_FIELD, building=B.BLD_MANOR, building_level=1),
    ]
    fief = {
        "id": 1,
        "name": "Усадьба Test",
        "hungry": 0,
        "might": B.MILITIA_FREE,
        "realm_id": 1,
    }
    db = MagicMock()
    db.get_fief.return_value = fief
    db.fief_tiles.return_value = tiles
    engine = Engine(db)
    with patch.object(engine, "fief_label", return_value="Усадьба Test"):
        with patch.object(
            engine, "fief_prod", return_value=Production(grain=5, goods=13, might=0)
        ):
            text = engine.holdings_text(1)
    assert "А1 Поле · Двор I" in text
    assert "сила двора не копится" in text
    assert "Итого:" in text
    db.fief_tiles.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_cb_holdings_shows_text_and_home():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    engine = MagicMock()
    engine.db.get_fief.return_value = fief
    engine.db.set_last_realm = MagicMock()
    engine.holdings_text.return_value = "HOLDINGS"
    home_kb = object()
    callback = MagicMock()
    callback.data = "hld:7"
    callback.from_user = MagicMock(id=100)
    callback.message = MagicMock()
    callback.answer = AsyncMock()

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod, "fief_home_kb", return_value=home_kb),
        patch.object(cb_mod, "reply_game", new_callable=AsyncMock) as reply,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_holdings(callback)

    engine.holdings_text.assert_called_once_with(7)
    reply.assert_awaited_once_with(
        callback.message,
        "HOLDINGS",
        reply_markup=home_kb,
    )


def test_estate_hub_kb_includes_holdings_button():
    from app.handlers.shared import estate_hub_kb

    kb = estate_hub_kb(7)
    datas = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
    ]
    assert "hld:7" in datas
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Владения" in t for t in labels)
