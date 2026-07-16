"""Хабы DM-меню и миграция со старой кнопки Ещё."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app.handlers import callbacks as cb_mod


def _callback(data: str, *, user_id: int = 100) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user = MagicMock(id=user_id)
    callback.message = MagicMock()
    callback.answer = AsyncMock()
    return callback


def _engine_for_fief(fief: dict) -> MagicMock:
    engine = MagicMock()
    engine.db.get_fief.return_value = fief
    engine.db.set_last_realm = MagicMock()
    engine.status_card.return_value = "STATUS"
    engine.rumors_text.return_value = "RUMORS"
    return engine


@pytest.mark.asyncio
async def test_cb_more_migrates_stale_more_button_to_new_home():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    engine = _engine_for_fief(fief)
    home_kb = object()
    callback = _callback("more:7")

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod, "fief_home_kb", return_value=home_kb) as home,
        patch.object(cb_mod, "reply_game", new_callable=AsyncMock) as reply,
    ):
        await cb_mod.cb_more(callback)

    callback.answer.assert_awaited_once_with("Меню обновлено")
    engine.db.set_last_realm.assert_called_once_with(100, 3)
    home.assert_called_once_with(engine, 7)
    reply.assert_awaited_once_with(
        callback.message,
        "STATUS",
        reply_markup=home_kb,
    )


@pytest.mark.asyncio
async def test_cb_hub_estate_opens_estate_keyboard():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    engine = _engine_for_fief(fief)
    estate_kb = object()
    callback = _callback("hub:e:7")

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(
            cb_mod, "fief_raid_pact_state", return_value=(True, None)
        ),
        patch.object(cb_mod, "estate_hub_kb", return_value=estate_kb) as estate,
        patch.object(cb_mod, "answer_html", new_callable=AsyncMock) as answer,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_hub(callback)

    estate.assert_called_once_with(7, raid_pact_open=True, lock_hint=None)
    answer.assert_awaited_once()
    assert "Усадьба" in answer.await_args.args[1]
    assert answer.await_args.kwargs["reply_markup"] is estate_kb


@pytest.mark.asyncio
async def test_cb_hub_valley_opens_valley_keyboard():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    engine = _engine_for_fief(fief)
    valley_kb = object()
    callback = _callback("hub:v:7")

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(
            cb_mod, "fief_raid_pact_state", return_value=(False, "после квестов")
        ),
        patch.object(cb_mod, "valley_hub_kb", return_value=valley_kb) as valley,
        patch.object(cb_mod, "answer_html", new_callable=AsyncMock) as answer,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_hub(callback)

    valley.assert_called_once_with(
        7, raid_pact_open=False, lock_hint="после квестов"
    )
    answer.assert_awaited_once()
    assert "Долина" in answer.await_args.args[1]
    assert answer.await_args.kwargs["reply_markup"] is valley_kb


@pytest.mark.asyncio
async def test_cb_hub_rejects_unknown_kind():
    callback = _callback("hub:x:7")
    engine = _engine_for_fief({"id": 7, "user_id": 100, "realm_id": 3})

    with patch.object(cb_mod, "get_engine", return_value=engine):
        await cb_mod.cb_hub(callback)

    callback.answer.assert_awaited_once_with(
        "Неизвестное меню", show_alert=True
    )


@pytest.mark.asyncio
async def test_cb_rumors_shows_text_and_home():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    engine = _engine_for_fief(fief)
    home_kb = object()
    callback = _callback("rum:7")

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod, "fief_home_kb", return_value=home_kb),
        patch.object(cb_mod, "reply_game", new_callable=AsyncMock) as reply,
        patch.object(cb_mod, "_ok", new_callable=AsyncMock),
    ):
        await cb_mod.cb_rumors(callback)

    engine.rumors_text.assert_called_once_with(3)
    reply.assert_awaited_once_with(
        callback.message,
        "RUMORS",
        reply_markup=home_kb,
    )


@pytest.mark.asyncio
async def test_cb_more_rejects_foreign_fief():
    engine = _engine_for_fief({"id": 7, "user_id": 999, "realm_id": 3})
    callback = _callback("more:7", user_id=100)

    with (
        patch.object(cb_mod, "get_engine", return_value=engine),
        patch.object(cb_mod, "reply_game", new_callable=AsyncMock) as reply,
    ):
        await cb_mod.cb_more(callback)

    callback.answer.assert_awaited_once_with(
        "Это не ваша усадьба", show_alert=True
    )
    reply.assert_not_awaited()
