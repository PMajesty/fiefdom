"""Публичные объявления долины идут в группу, не в личный fan-out."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.raids import RaidActionResult
from app.handlers.dm import _handle_pending


def _raid_result(**overrides) -> RaidActionResult:
    base = dict(
        public_line="Альфа ограбила Бета",
        success=True,
        victim_fief_id=2,
        victim_user_id=200,
        victim_name="Бета",
        attacker_name="Альфа",
        stolen={B.RES_GRAIN: 3, B.RES_GOODS: 1},
        intercept_applied=False,
        attacker_realm_id=1,
        victim_realm_id=1,
        via_portal=False,
        attacker_public_line="Альфа ограбила Бета",
        victim_public_line="",
    )
    base.update(overrides)
    return RaidActionResult(**base)


@pytest.mark.asyncio
async def test_raid_logs_to_attacker_valley_group():
    engine = MagicMock()
    result = _raid_result()
    engine.raid = MagicMock(return_value=result)

    bot = MagicMock()
    bot.send_message = AsyncMock()
    message = MagicMock()
    message.bot = bot
    message.from_user = MagicMock(id=100)

    pending = {"kind": "raid_might", "fief_id": 1, "victim_id": 2}

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply,
        patch("app.handlers.dm.post_realm_public", new_callable=AsyncMock) as public,
        patch("app.handlers.dm._notify_raid_parties", new_callable=AsyncMock) as parties,
        patch("app.handlers.dm.clear_pending"),
        patch("app.handlers.dm.fief_home_kb", return_value=None),
    ):
        ok = await _handle_pending(message, engine, pending, "10")

    assert ok is True
    parties.assert_awaited_once()
    public.assert_awaited_once()
    assert public.await_args.args[1] == 1
    assert "⚔️" in public.await_args.args[2]
    assert "зерна" not in public.await_args.args[2]
    assert "товаров" not in public.await_args.args[2]
    private = reply.await_args.args[1]
    assert "+3 зерна" in private
    assert "+1 товаров" in private


@pytest.mark.asyncio
async def test_cross_valley_raid_logs_to_both_groups():
    engine = MagicMock()
    result = _raid_result(
        via_portal=True,
        victim_realm_id=2,
        attacker_public_line='В "Чужая": Альфа ограбила Бета',
        victim_public_line='Из "Домашняя": Альфа ограбила Бета',
    )
    engine.raid = MagicMock(return_value=result)

    bot = MagicMock()
    message = MagicMock()
    message.bot = bot
    message.from_user = MagicMock(id=100)

    pending = {"kind": "raid_might", "fief_id": 1, "victim_id": 2}

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock),
        patch("app.handlers.dm.post_realm_public", new_callable=AsyncMock) as public,
        patch("app.handlers.dm._notify_raid_parties", new_callable=AsyncMock),
        patch("app.handlers.dm.clear_pending"),
        patch("app.handlers.dm.fief_home_kb", return_value=None),
    ):
        ok = await _handle_pending(message, engine, pending, "10")

    assert ok is True
    assert public.await_count == 2
    realm_ids = [c.args[1] for c in public.await_args_list]
    assert realm_ids == [1, 2]


@pytest.mark.asyncio
async def test_pact_create_logs_to_group():
    engine = MagicMock()
    fief = {"id": 1, "realm_id": 7, "name": "Альфа"}
    engine.db.get_fief = MagicMock(return_value=fief)
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
