"""Прямая передача / караваны ресурсов на доверии."""
from __future__ import annotations

import os
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.caravans import DeclareCaravanResult
from app.engine import Engine
from app.handlers.dm import _handle_pending, _parse_send_line
from app.handlers.shared import format_send_announce


def test_parse_send_line():
    assert _parse_send_line("зерно 10") == (B.RES_GRAIN, 10)
    assert _parse_send_line("товары 5") == (B.RES_GOODS, 5)
    assert _parse_send_line("goods 3") == (B.RES_GOODS, 3)
    assert _parse_send_line("зерно 10 товары 5") is None
    assert _parse_send_line("сила 5") is None


def test_format_send_announce():
    text = format_send_announce("Альфа", "Бета", 12, B.RES_GRAIN)
    assert "Альфа" in text
    assert "Бета" in text
    assert "12" in text
    assert "Зерно" in text or "зерно" in text.lower()


@pytest.mark.asyncio
async def test_send_amount_declares_caravan_and_dms_receiver():
    """Мелкий обоз - ЛС получателю, без анонса в долину; кнопка вернуть."""
    engine, sender, receiver = _engine_pair()
    result = DeclareCaravanResult(
        intent_id=7,
        receiver_fief_id=2,
        receiver_name="Бета",
        res=B.RES_GRAIN,
        amt=10,
        is_public=False,
        dm_text="Обоз ушёл к Бета: 10 Зерно в пути.",
        receiver_dm_text="К вам идёт обоз от Альфа: 10 Зерно.",
        public_declare_text=None,
    )
    engine.declare_caravan = MagicMock(return_value=result)
    engine.ensure_user = MagicMock()

    bot = MagicMock()
    bot.send_message = AsyncMock()
    message = MagicMock()
    message.bot = bot
    message.from_user = MagicMock(id=100)

    pending = {
        "kind": "send_amount",
        "fief_id": 1,
        "target_fief_id": 2,
    }

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply,
        patch("app.handlers.dm.post_continent_public", new_callable=AsyncMock) as post_pub,
        patch("app.handlers.dm.clear_pending") as clear,
        patch("app.handlers.dm.caravan_cancel_intent_kb", return_value="kb") as cancel_kb,
    ):
        ok = await _handle_pending(message, engine, pending, "зерно 10")

    assert ok is True
    clear.assert_called_once_with(100)
    engine.declare_caravan.assert_called_once_with(1, 2, B.RES_GRAIN, 10)
    reply.assert_awaited_once()
    assert reply.await_args.args[1] == result.dm_text
    cancel_kb.assert_called_once_with(1, 7)
    post_pub.assert_not_called()
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == 200
    assert bot.send_message.await_args.args[1] == result.receiver_dm_text
    assert sender["id"] == 1
    assert receiver["id"] == 2


@pytest.mark.asyncio
async def test_send_amount_public_caravan_posts_group_chats():
    engine, _sender, _receiver = _engine_pair()
    result = DeclareCaravanResult(
        intent_id=8,
        receiver_fief_id=2,
        receiver_name="Бета",
        res=B.RES_GOODS,
        amt=B.CARAVAN_PUBLIC_AMOUNT,
        is_public=True,
        dm_text="Обоз ушёл.",
        receiver_dm_text="Обоз идёт.",
        public_declare_text="📦 Обоз: Альфа шлёт товары.",
    )
    engine.declare_caravan = MagicMock(return_value=result)
    engine.ensure_user = MagicMock()

    bot = MagicMock()
    bot.send_message = AsyncMock()
    message = MagicMock()
    message.bot = bot
    message.from_user = MagicMock(id=100)

    pending = {
        "kind": "send_amount",
        "fief_id": 1,
        "target_fief_id": 2,
    }

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock),
        patch("app.handlers.dm.post_continent_public", new_callable=AsyncMock) as post_pub,
        patch("app.handlers.dm.clear_pending"),
        patch("app.handlers.dm.caravan_cancel_intent_kb", return_value=None),
    ):
        ok = await _handle_pending(
            message, engine, pending, f"товары {B.CARAVAN_PUBLIC_AMOUNT}"
        )

    assert ok is True
    post_pub.assert_awaited_once_with(
        bot, 10, result.public_declare_text
    )


def _engine_pair(*, grain_from=50, goods_from=40, grain_to=5, goods_to=5, barn=0):
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    sender = {
        "id": 1,
        "realm_id": 10,
        "grain": grain_from,
        "goods": goods_from,
        "frozen": False,
        "name": "Альфа",
        "user_id": 100,
    }
    receiver = {
        "id": 2,
        "realm_id": 10,
        "grain": grain_to,
        "goods": goods_to,
        "frozen": False,
        "name": "Бета",
        "user_id": 200,
    }
    fiefs = {1: sender, 2: receiver}

    def get_fief(fid):
        row = fiefs.get(int(fid))
        return dict(row) if row is not None else None

    def debit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    intents: list[dict] = []

    def create_action_intent(**fields):
        row = {
            "id": len(intents) + 1,
            "world_id": fields["world_id"],
            "tick_index": fields["tick_index"],
            "fief_id": fields["fief_id"],
            "kind": fields["kind"],
            "payload": dict(fields.get("payload") or {}),
            "status": fields.get("status", "open"),
        }
        intents.append(row)
        return dict(row)

    db.get_fief.side_effect = get_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.create_action_intent.side_effect = create_action_intent
    db.realms_are_adjacent.return_value = True
    db.get_realm.return_value = {"id": 10, "world_id": 1, "tick_index": 5}

    engine = Engine(db)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=barn)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine._require_cross_valley_caught_up = MagicMock()
    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._format_raid_deadline = MagicMock(return_value="-")
    db.get_world.return_value = {"id": 1, "tick_index": 5}
    engine._fiefs = fiefs
    engine._intents = intents
    return engine, sender, receiver


def test_declare_caravan_grain_ok():
    engine, sender, receiver = _engine_pair()
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    assert sender["grain"] == 40
    assert receiver["grain"] == 5
    assert result.amt == 10
    assert result.receiver_name == "Бета"
    assert result.is_public is False
    assert len(engine._intents) == 1
    intent = engine._intents[0]
    assert intent["kind"] == "caravan"
    assert intent["payload"]["receiver_id"] == 2
    assert intent["payload"]["res"] == B.RES_GRAIN
    assert intent["payload"]["amt"] == 10
    assert intent["payload"]["escrowed"] is True
    engine.db.debit_fief_resources.assert_called()
    engine.db.create_action_intent.assert_called_once()


def test_declare_caravan_rejects_might():
    engine, _, _ = _engine_pair()
    with pytest.raises(ValueError) as exc:
        engine.declare_caravan(1, 2, B.RES_MIGHT, 5)
    assert "зерно" in str(exc.value).lower() or "товар" in str(exc.value).lower()
    engine.db.create_action_intent.assert_not_called()


def test_declare_caravan_rejects_self():
    engine, _, _ = _engine_pair()
    with pytest.raises(ValueError, match="себе"):
        engine.declare_caravan(1, 1, B.RES_GRAIN, 5)
    engine.db.create_action_intent.assert_not_called()


def test_declare_caravan_rejects_insufficient():
    engine, _, _ = _engine_pair(grain_from=3)
    with pytest.raises(ValueError, match="Недостаточно"):
        engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    engine.db.create_action_intent.assert_not_called()


def test_declare_caravan_allowed_when_receiver_full():
    """Declare не проверяет склад получателя - bounce будет на resolve."""
    engine, sender, receiver = _engine_pair(grain_to=B.stash_cap(0))
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 1)
    assert result.intent_id == 1
    assert sender["grain"] == 49
    assert receiver["grain"] == B.stash_cap(0)
    assert engine._intents[0]["payload"]["amt"] == 1
