"""Публичные объявления долины идут в группу, не в личный fan-out."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app.domain.raids import DeclareRaidResult
from app.handlers.dm import _handle_pending


@pytest.mark.asyncio
async def test_raid_might_prompts_confirm_without_instant_group():
    """Ввод силы не резолвит бой и не постит исход в группу."""
    engine = MagicMock()
    engine.fief_by_id.side_effect = lambda fid: {
        1: {"id": 1, "realm_id": 1, "might": 40, "pact_id": None},
        2: {"id": 2, "realm_id": 1, "name": "Бета"},
    }.get(fid)
    engine.fief_label = MagicMock(return_value="Бета")
    engine.world_id_for_realm = MagicMock(return_value=1)
    engine.world.return_value = {"id": 1}
    engine.format_raid_deadline = MagicMock(return_value="17.07 12:00")

    bot = MagicMock()
    message = MagicMock()
    message.bot = bot
    message.from_user = MagicMock(id=100)

    pending = {"kind": "raid_might", "fief_id": 1, "victim_id": 2}

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply,
        patch("app.handlers.dm.post_realm_public", new_callable=AsyncMock) as public,
        patch("app.handlers.dm.set_pending") as set_pending,
        patch("app.handlers.dm.raid_confirm_kb", return_value=None),
    ):
        ok = await _handle_pending(message, engine, pending, "10")

    assert ok is True
    public.assert_not_awaited()
    engine.declare_raid.assert_not_called()
    assert set_pending.called
    assert "Подтвердите" in reply.await_args.args[1]
    assert "10" in reply.await_args.args[1]


@pytest.mark.asyncio
async def test_declare_result_has_no_loot_digits_in_public_copy():
    """Declare DM - без цифр добычи; исход публикуется только после resolve."""
    result = DeclareRaidResult(
        intent_id=1,
        victim_fief_id=2,
        victim_name="Бета",
        might=10,
        men_home=5,
        open_truce=False,
        lock_deadline_text="12:00",
        resolve_slot_text="14:00",
        dm_text="Дружина ушла в ночь на хутор Бета: 10 силы в пути, дома 5.",
    )
    assert "зерна" not in result.dm_text
    assert "товаров" not in result.dm_text


@pytest.mark.asyncio
async def test_pact_create_logs_to_group():
    engine = MagicMock()
    fief = {"id": 1, "realm_id": 7, "name": "Альфа"}
    engine.fief_by_id = MagicMock(return_value=fief)
    engine.create_pact = MagicMock(return_value="Пакт создан.")
    engine.fief_label = MagicMock(return_value="Альфа")
    engine.ensure_user = MagicMock()

    message = MagicMock()
    message.bot = MagicMock()
    message.from_user = MagicMock(id=100)

    pending = {"kind": "pact_name", "fief_id": 1}

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock),
        patch("app.handlers.dm.post_realm_public", new_callable=AsyncMock) as public,
        patch("app.handlers.dm.clear_pending"),
        patch("app.handlers.dm.fief_home_kb", return_value=None),
    ):
        ok = await _handle_pending(message, engine, pending, "Север")

    assert ok is True
    public.assert_awaited_once()
    assert public.await_args.args[1] == 7
    assert "Север" in public.await_args.args[2]


def test_guide_mentions_public_drama_channel():
    from app.domain.guide import game_guide

    text = game_guide()
    assert "набеги, пакты, беды и указы" in text
    assert "беды и прочие извещения - в личке" not in text
