"""Голод: сторожки молчат, роспуск дружины, сбор силы."""
from __future__ import annotations

import pytest

from app import balance as B
from app.domain.guide import game_guide
from app.domain.hunger import (
    gather_might_hungry_message,
    hunger_holdings_banner,
    hunger_status_alert,
)
from app.domain.holdings import format_holdings, tile_effect_text
from app.domain.production import TileView, fief_daily_production
from app.domain.tick import FiefTickState, _pay_grain, apply_fief_tick
from app.services.land_actions import LandActionService


def test_pay_grain_fractional_pending_does_not_fake_full_payment():
    grain, pending, paid = _pay_grain(0, 2.5, 12)
    assert grain == 0
    assert pending == pytest.approx(0.0)
    assert paid == 2
    assert paid < 12


def test_militia_after_disband():
    assert B.militia_after_disband(20, 5) == (5, 15)
    assert B.militia_after_disband(3, 5) == (3, 0)
    assert B.militia_after_disband(10, 0) == (0, 10)


def test_militia_billable_and_keep_respect_prepaid_and_free_band():
    assert B.militia_billable_might(47, prepaid_might=42) == 5
    assert B.militia_billable_might(40, prepaid_might=40) == 0
    assert B.militia_upkeep_grain(B.militia_billable_might(40, 40)) == 0
    # Весь prepaid сохранён; без зерна у домашних остаётся бесплатная полоса.
    keep, lost = B.militia_keep_after_shortfall(
        50, paid_grain=0, need_grain=3, prepaid_might=40
    )
    assert keep == 40 + B.MILITIA_FREE
    assert lost == 5
    # Полный prepaid: утром после возврата никого не режем.
    keep_all, lost_all = B.militia_keep_after_shortfall(
        40, paid_grain=0, need_grain=0, prepaid_might=40
    )
    assert keep_all == 40
    assert lost_all == 0


def test_tick_prepaid_return_skips_militia_disband_keeps_free_home():
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    # Вернулись 40 с похода, дома ещё 10 - без зерна режем только домашних сверх 5.
    state = FiefTickState(
        stash={B.RES_GRAIN: 0, B.RES_GOODS: 0, B.RES_MIGHT: 50},
        pending={B.RES_GRAIN: 0.0, B.RES_GOODS: 0.0, B.RES_MIGHT: 0.0},
        actions=1,
        hungry=False,
        tiles=tiles,
        barn_level=0,
        militia_prepaid_might=40,
    )
    out = apply_fief_tick(state)
    assert out.stash[B.RES_MIGHT] == 40 + B.MILITIA_FREE
    assert out.militia_disbanded == 5
    assert out.militia_upkeep == B.militia_upkeep_grain(10)


def test_tick_full_prepaid_return_survives_empty_granary():
    tiles = [TileView(0, 0, B.TILE_HILLS, 1, None, 0)]
    state = FiefTickState(
        stash={B.RES_GRAIN: 0, B.RES_GOODS: 0, B.RES_MIGHT: 42},
        pending={B.RES_GRAIN: 0.0, B.RES_GOODS: 0.0, B.RES_MIGHT: 0.0},
        actions=1,
        hungry=False,
        tiles=tiles,
        barn_level=0,
        militia_prepaid_might=42,
    )
    out = apply_fief_tick(state)
    assert out.stash[B.RES_MIGHT] == 42
    assert out.militia_disbanded == 0
    assert out.militia_upkeep == 0
    # Следующий тик без prepaid - полный роспуск до бесплатных 5.
    next_state = FiefTickState(
        stash=dict(out.stash),
        pending=dict(out.pending),
        actions=out.actions,
        hungry=out.hungry,
        tiles=tiles,
        barn_level=0,
        militia_prepaid_might=0,
    )
    next_out = apply_fief_tick(next_state)
    assert next_out.stash[B.RES_MIGHT] == B.MILITIA_FREE
    assert next_out.militia_disbanded == 42 - B.MILITIA_FREE


def test_hungry_zeros_watch_might_keeps_full_manor_refill():
    tiles = [
        TileView(0, 0, B.TILE_FIELD, 1, B.BLD_MANOR, 1),
        TileView(1, 0, B.TILE_HILLS, 1, B.BLD_WATCH, 1),
    ]
    fed = fief_daily_production(tiles, current_might=0)
    hungry = fief_daily_production(tiles, hungry=True, current_might=0)
    watch = B.WATCH_MIGHT[1] * B.NATIVE_BONUS
    assert fed.resources()[B.RES_MIGHT] == pytest.approx(B.MANOR_MIGHT + watch)
    assert hungry.resources()[B.RES_MIGHT] == pytest.approx(float(B.MANOR_MIGHT))
    assert hungry.resources()[B.RES_GRAIN] == pytest.approx(
        B.MANOR_GRAIN * B.HUNGER_PRODUCTION_MULT
    )
    assert hungry.defense == pytest.approx(fed.defense)


def test_hungry_manor_refill_respects_free_cap():
    tiles = [TileView(0, 0, B.TILE_FIELD, 1, B.BLD_MANOR, 1)]
    at_cap = fief_daily_production(
        tiles, hungry=True, current_might=B.MILITIA_FREE
    )
    assert at_cap.resources()[B.RES_MIGHT] == 0
    room_one = fief_daily_production(
        tiles, hungry=True, current_might=B.MILITIA_FREE - 1
    )
    assert room_one.resources()[B.RES_MIGHT] == pytest.approx(1.0)


def test_tick_hungry_pending_might_only_today_manor():
    tiles = [
        TileView(0, 0, B.TILE_FIELD, 1, B.BLD_MANOR, 1),
        TileView(1, 0, B.TILE_HILLS, 1, B.BLD_WATCH, 1),
        TileView(2, 0, B.TILE_HILLS, 1, None, 0),
        TileView(3, 0, B.TILE_HILLS, 1, None, 0),
        TileView(4, 0, B.TILE_HILLS, 1, None, 0),
    ]
    state = FiefTickState(
        stash={B.RES_GRAIN: 0, B.RES_GOODS: 0, B.RES_MIGHT: 3},
        pending={
            B.RES_GRAIN: 0.0,
            B.RES_GOODS: 0.0,
            B.RES_MIGHT: 40.0,
        },
        actions=1,
        hungry=True,
        tiles=tiles,
        barn_level=0,
    )
    out = apply_fief_tick(state)
    assert out.hungry is True
    free_room = max(0, B.MILITIA_FREE - int(out.stash[B.RES_MIGHT]))
    assert out.pending[B.RES_MIGHT] == pytest.approx(
        float(min(B.MANOR_MIGHT, free_room))
    )
    assert out.production.resources()[B.RES_MIGHT] == pytest.approx(
        float(B.MANOR_MIGHT)
    )


def test_tick_newly_hungry_drops_watch_keeps_manor_room():
    # Много клеток → содержание земли выше сытого урожая двора.
    tiles = [
        TileView(0, 0, B.TILE_FIELD, 1, B.BLD_MANOR, 1),
        TileView(1, 0, B.TILE_HILLS, 1, B.BLD_WATCH, 1),
        TileView(2, 0, B.TILE_HILLS, 1, None, 0),
        TileView(3, 0, B.TILE_HILLS, 1, None, 0),
        TileView(4, 0, B.TILE_HILLS, 1, None, 0),
    ]
    state = FiefTickState(
        stash={B.RES_GRAIN: 0, B.RES_GOODS: 0, B.RES_MIGHT: 3},
        pending={
            B.RES_GRAIN: 0.0,
            B.RES_GOODS: 0.0,
            B.RES_MIGHT: 25.0,
        },
        actions=1,
        hungry=False,
        tiles=tiles,
        barn_level=0,
    )
    out = apply_fief_tick(state)
    assert out.hungry is True
    # Сытое производство сторожки не остаётся; добор двора к итоговой дружине.
    free_room = max(0, B.MILITIA_FREE - int(out.stash[B.RES_MIGHT]))
    assert out.pending[B.RES_MIGHT] == pytest.approx(
        float(min(B.MANOR_MIGHT, free_room))
    )
    assert out.pending[B.RES_MIGHT] < 25.0


def test_disband_collects_pending_before_cut():
    store = {
        "id": 1,
        "hungry": False,
        "frozen": False,
        "might": 10,
        "grain": 0,
        "goods": 0,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 8.0,
    }
    collected = {"n": 0}

    class _Db:
        def get_fief(self, _fid):
            return dict(store)

        def update_fief(self, _fid, **patch):
            store.update(patch)

    class _Eng:
        def collect_for_fief(self, _fid, **_kw):
            collected["n"] += 1
            store["might"] = int(store["might"]) + int(store["pending_might"])
            store["pending_might"] = 0.0
            return []

    svc = LandActionService(_Eng(), _Db())
    msg = svc.disband_militia(1, B.MILITIA_FREE)
    assert collected["n"] == 1
    assert store["might"] == B.MILITIA_FREE
    assert store["pending_might"] == 0.0
    assert "Распустил 13" in msg  # 10+8 → 18, keep 5 → lost 13


def test_tile_effect_hungry_watch_hides_might():
    watch = {
        "x": 0,
        "y": 0,
        "tile_type": B.TILE_HILLS,
        "building": B.BLD_WATCH,
        "building_level": 1,
        "is_overgrown": False,
    }
    text = tile_effect_text(watch, hungry=True)
    assert "силы/день" not in text
    assert "защиты" in text or "защит" in text


def test_tile_effect_hungry_manor_keeps_full_might():
    manor = {
        "x": 0,
        "y": 0,
        "tile_type": B.TILE_FIELD,
        "building": B.BLD_MANOR,
        "building_level": 1,
        "is_overgrown": False,
    }
    text = tile_effect_text(manor, hungry=True, current_might=0)
    halved_grain = int(B.MANOR_GRAIN * B.HUNGER_PRODUCTION_MULT)
    assert f"+{halved_grain} зерна/день" in text
    assert f"+{B.MANOR_MIGHT} силы/день" in text


def test_holdings_hungry_banner_mentions_might():
    text = format_holdings(
        [],
        fief_label="Усадьба X",
        hungry=True,
    )
    assert hunger_holdings_banner() in text
    assert str(B.MILITIA_FREE) in text


def test_hunger_status_alert_mentions_disband():
    alert = hunger_status_alert()
    assert "падает вдвое" in alert
    assert "сторожки и сбор силы молчат" in alert
    assert "по-прежнему добирает" in alert
    assert str(B.MILITIA_FREE) in alert
    assert "набег и заставу" in alert
    assert "распустить" in alert
    assert "собрать зерно" in alert
    assert "обоз" in alert
    assert "Урожай вдвое," not in alert
    assert "сила со стороны" not in alert


def test_hunger_holdings_banner_matches_status_polarity():
    banner = hunger_holdings_banner()
    assert "падает вдвое" in banner
    assert "сторожки и сбор силы молчат" in banner
    assert "по-прежнему добирает" in banner
    assert str(B.MILITIA_FREE) in banner
    assert "сила со стороны" not in banner


def test_guide_hunger_section_matches_status_rules():
    text = game_guide()
    hunger_block = text.split("Голод\n", 1)[1].split("\n\n", 1)[0]
    assert "падает вдвое" in hunger_block
    assert "сторожки и сбор силы молчат" in hunger_block
    assert "набег и заставу" in hunger_block
    assert "по-прежнему добирает" in hunger_block
    assert str(B.MILITIA_FREE) in hunger_block
    assert "обоз" in hunger_block
    assert "сила со стороны" not in hunger_block
    assert "Урожай вдвое" not in hunger_block


def test_gather_might_blocked_when_hungry():
    class _Db:
        def get_fief(self, _fid):
            return {"id": 1, "hungry": True, "frozen": False, "actions": 2}

        def transaction(self):
            raise AssertionError("не должны тратить действие")

    class _Eng:
        def collect_for_fief(self, *_a, **_k):
            raise AssertionError("collect не нужен до проверки голода")

    svc = LandActionService(_Eng(), _Db())
    with pytest.raises(ValueError, match=gather_might_hungry_message()):
        svc.gather_resource(1, B.RES_MIGHT)


def test_disband_militia_cuts_stash():
    store = {
        "id": 1,
        "hungry": False,
        "frozen": False,
        "might": 20,
    }

    class _Db:
        def get_fief(self, _fid):
            return dict(store)

        def update_fief(self, _fid, **patch):
            store.update(patch)

    class _Eng:
        def collect_for_fief(self, _fid, **_kw):
            return []

    svc = LandActionService(_Eng(), _Db())
    msg = svc.disband_militia(1, B.MILITIA_FREE)
    assert store["might"] == B.MILITIA_FREE
    assert "Распустил 15" in msg
    assert f"корм дружины {B.militia_upkeep_grain(B.MILITIA_FREE)}" in msg


def test_disband_prompt_asks_for_keep_number():
    from app.handlers.dm import disband_prompt_text

    text = disband_prompt_text(20, hungry=True)
    assert "Дружина дома: 20" in text
    assert "от 0 до 19" in text
    assert "отмена" in text.lower()
    assert "голод" in text.lower()


@pytest.mark.asyncio
async def test_disband_keep_pending_applies_number():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 20, "id": 3}
    engine.disband_militia.return_value = "Распустил 12 (−12 Силы)."
    engine.status_card.return_value = "статус"
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply,
        patch("app.handlers.dm.clear_pending") as clear_pending,
        patch("app.handlers.dm.fief_home_kb", return_value="home"),
    ):
        ok = await _handle_pending(message, engine, pending, "8")
    assert ok is True
    engine.disband_militia.assert_called_once_with(3, 8)
    clear_pending.assert_called_once_with(101)
    assert "Распустил 12" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_disband_keep_pending_rejects_out_of_range():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 20, "id": 3}
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.answer_html", new_callable=AsyncMock) as answer,
        patch("app.handlers.dm.pending_cancel_kb", return_value="cancel"),
    ):
        ok = await _handle_pending(message, engine, pending, "20")
    assert ok is True
    engine.disband_militia.assert_not_called()
    text = answer.await_args.args[1]
    assert "от 0 до 19" in text


@pytest.mark.asyncio
async def test_disband_keep_pending_rejects_non_numeric():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 20, "id": 3}
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.answer_html", new_callable=AsyncMock) as answer,
        patch("app.handlers.dm.pending_cancel_kb", return_value="cancel"),
        patch("app.handlers.dm.clear_pending") as clear_pending,
    ):
        ok = await _handle_pending(message, engine, pending, "abc")
    assert ok is True
    engine.disband_militia.assert_not_called()
    clear_pending.assert_not_called()
    assert "от 0 до 19" in answer.await_args.args[1]


@pytest.mark.asyncio
async def test_disband_keep_pending_engine_error_keeps_pending():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 20, "id": 3}
    engine.disband_militia.side_effect = ValueError("Усадьба заморожена")
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.answer_html", new_callable=AsyncMock) as answer,
        patch("app.handlers.dm.pending_cancel_kb", return_value="cancel"),
        patch("app.handlers.dm.clear_pending") as clear_pending,
    ):
        ok = await _handle_pending(message, engine, pending, "5")
    assert ok is True
    clear_pending.assert_not_called()
    assert "Усадьба заморожена" in answer.await_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", ["0", "19"])
async def test_disband_keep_pending_boundary_values(keep: str):
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 20, "id": 3}
    engine.disband_militia.return_value = "ok"
    engine.status_card.return_value = "статус"
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock),
        patch("app.handlers.dm.clear_pending"),
        patch("app.handlers.dm.fief_home_kb", return_value="home"),
    ):
        ok = await _handle_pending(message, engine, pending, keep)
    assert ok is True
    engine.disband_militia.assert_called_once_with(3, int(keep))


@pytest.mark.asyncio
async def test_cb_disband_sets_pending_and_prompt():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers import callbacks as cb_mod
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.require_owned_fief.return_value = {
        "id": 3,
        "might": 20,
        "hungry": False,
        "militia_prepaid_might": 0,
    }
    engine.fief_by_id.return_value = {
        "id": 3,
        "might": 20,
        "hungry": False,
        "militia_prepaid_might": 0,
    }
    callback = MagicMock()
    callback.data = "dis:3"
    callback.from_user = MagicMock(id=101)
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod.dm_mod, "set_pending") as set_pending,
        patch.object(cb_mod.dm_mod, "pending_cancel_kb", return_value="cancel"),
        patch.object(cb_mod, "answer_html", new_callable=AsyncMock) as answer,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_disband(callback)
    set_pending.assert_called_once_with(
        101, {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    )
    assert "от 0 до 19" in answer.await_args.args[1]


@pytest.mark.asyncio
async def test_cb_disband_no_militia_alerts_without_pending():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers import callbacks as cb_mod

    engine = MagicMock()
    engine.require_owned_fief.return_value = {"id": 3, "might": 0}
    engine.fief_by_id.return_value = {"id": 3, "might": 0}
    callback = MagicMock()
    callback.data = "dis:3"
    callback.from_user = MagicMock(id=101)
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod.dm_mod, "set_pending") as set_pending,
        patch.object(cb_mod, "answer_html", new_callable=AsyncMock) as answer,
    ):
        await cb_mod.cb_disband(callback)
    set_pending.assert_not_called()
    answer.assert_not_called()
    callback.answer.assert_awaited()
    assert "Некого распускать" in callback.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_disband_keep_pending_clears_when_no_militia():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers.dm import _handle_pending
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.fief_by_id.return_value = {"might": 0, "id": 3}
    message = MagicMock()
    message.from_user = MagicMock(id=101)
    pending = {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    with (
        patch("app.handlers.dm.answer_html", new_callable=AsyncMock) as answer,
        patch("app.handlers.dm.clear_pending") as clear_pending,
        patch("app.handlers.dm.fief_home_kb", return_value="home"),
    ):
        ok = await _handle_pending(message, engine, pending, "0")
    assert ok is True
    clear_pending.assert_called_once_with(101)
    engine.disband_militia.assert_not_called()
    assert "Некого распускать" in answer.await_args.args[1]


@pytest.mark.asyncio
async def test_cb_disband_stale_ok_opens_prompt():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.handlers import callbacks as cb_mod
    from app.ui.pending import KIND_DISBAND_KEEP

    engine = MagicMock()
    engine.require_owned_fief.return_value = {
        "id": 3,
        "might": 12,
        "hungry": False,
        "militia_prepaid_might": 0,
    }
    engine.fief_by_id.return_value = {
        "id": 3,
        "might": 12,
        "hungry": False,
        "militia_prepaid_might": 0,
    }
    callback = MagicMock()
    callback.data = f"dis:3:{B.MILITIA_FREE}:ok"
    callback.from_user = MagicMock(id=101)
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod.dm_mod, "set_pending") as set_pending,
        patch.object(cb_mod.dm_mod, "pending_cancel_kb", return_value="cancel"),
        patch.object(cb_mod, "answer_html", new_callable=AsyncMock) as answer,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_disband(callback)
    set_pending.assert_called_once_with(
        101, {"kind": KIND_DISBAND_KEEP, "fief_id": 3}
    )
    assert "от 0 до 11" in answer.await_args.args[1]


def test_gather_resources_kb_hides_might_when_hungry():
    from app.ui.keyboards import gather_resources_kb

    fed = gather_resources_kb(2, hungry=False)
    hungry = gather_resources_kb(2, hungry=True)
    fed_data = [btn.callback_data for row in fed.inline_keyboard for btn in row]
    hungry_data = [
        btn.callback_data for row in hungry.inline_keyboard for btn in row
    ]
    assert f"gth:2:{B.RES_MIGHT}" in fed_data
    assert f"gth:2:{B.RES_MIGHT}" not in hungry_data


def test_realm_tick_clears_prepaid_for_active_and_inactive():
    from contextlib import nullcontext

    from app.services.realm_tick import RealmTickRunner

    fiefs = {
        1: {
            "id": 1,
            "realm_id": 1,
            "frozen": False,
            "actions": 1,
            "hungry": False,
            "grain": 20,
            "goods": 0,
            "might": 40,
            "pending_grain": 0.0,
            "pending_goods": 0.0,
            "pending_might": 0.0,
            "militia_prepaid_might": 35,
            "patrol_until_tick": None,
            "shield_until_tick": None,
            "patrol_until": None,
            "shield_until": None,
        },
        2: {
            "id": 2,
            "realm_id": 1,
            "frozen": False,
            "actions": 1,
            "hungry": False,
            "grain": 0,
            "goods": 0,
            "might": 20,
            "pending_grain": 0.0,
            "pending_goods": 0.0,
            "pending_might": 0.0,
            "militia_prepaid_might": 18,
            "patrol_until_tick": None,
            "shield_until_tick": None,
            "patrol_until": None,
            "shield_until": None,
        },
    }
    updates: list[tuple[int, dict]] = []

    class _Db:
        def get_realm(self, _rid):
            return {
                "id": 1,
                "title": "Долина",
                "tick_index": 3,
                "day_number": 3,
                "timezone": "UTC",
                "chat_id": -100,
                "pending_raid_lines": [],
                "active_minor_key": None,
            }

        def list_fiefs(self, _rid):
            return [dict(fiefs[1]), dict(fiefs[2])]

        def fief_tiles(self, fid):
            return [
                {
                    "x": 0,
                    "y": 0,
                    "tile_type": B.TILE_FIELD,
                    "owner_fief_id": int(fid),
                    "building": B.BLD_MANOR,
                    "building_level": 1,
                    "is_core": True,
                    "is_overgrown": False,
                }
            ]

        def update_fief(self, fid, **fields):
            fiefs[int(fid)].update(fields)
            updates.append((int(fid), dict(fields)))

        def update_realm(self, _rid, **_fields):
            return None

        def transaction(self):
            return nullcontext()

    class _Eng:
        def apply_absence(self, _rid):
            return None

        def _resolve_tile_entities(self, _rid, _tick):
            return [], []

        def _prepare_tick_minor(self, _rid, consume_pending=True):
            return None

        def realm_modifiers(self, _realm, tile_entities=None):
            class _M:
                def farm_mult(self):
                    return 1.0

            return _M()

        def barn_level(self, _fid):
            return 0

        def fief_is_active_play(self, fief):
            return int(fief["id"]) == 1

        def _feud_lines(self, _rid):
            return []

        def maybe_grow_map(self, _rid):
            return None

        def _sunday_extra(self, _rid):
            return None

    RealmTickRunner(_Eng(), _Db()).run_realm_tick(1, advance_clock=False)
    assert fiefs[1]["militia_prepaid_might"] == 0
    assert fiefs[2]["militia_prepaid_might"] == 0
    assert any(
        fid == 2 and fields.get("militia_prepaid_might") == 0
        for fid, fields in updates
    )
