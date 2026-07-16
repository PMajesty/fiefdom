"""Админ-роутер должен получать slash-команды раньше dm catch-all."""
from __future__ import annotations

import os

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest
from aiogram import Dispatcher
from aiogram.types import Chat, Message, User

from app.handlers import admin, dm
from app.handlers.admin import ADMIN_HELP_TEXT, format_realms_list
from app.main import register_routers


def test_admin_router_registered_before_dm():
    dp = Dispatcher()
    register_routers(dp)
    assert dp.sub_routers.index(admin.router) < dp.sub_routers.index(dm.router)


def test_admin_help_explains_wipe_two_steps():
    assert "/вч_realms" in ADMIN_HELP_TEXT
    assert "/вч_wipe_start" in ADMIN_HELP_TEXT
    assert "два шага" in ADMIN_HELP_TEXT
    assert "/вч_grant 1 3" in ADMIN_HELP_TEXT
    assert "\u2014" not in ADMIN_HELP_TEXT


def test_format_realms_list_empty():
    assert format_realms_list([], {}) == "Нет долин."


def test_format_realms_list_shows_ids_and_counts():
    text = format_realms_list(
        [{"id": 3, "title": "Север <Долина>", "chat_id": -100123}],
        {3: 2},
    )
    assert "#3" in text
    assert "Север &lt;Долина&gt;" in text
    assert "chat_id=<code>-100123</code>" in text
    assert "усадеб: 2" in text
    assert "\u2014" not in text
    assert "\u00ab" not in text


@pytest.mark.asyncio
async def test_dm_text_filter_rejects_slash_commands():
    """Catch-all не должен матчить /команды - иначе admin не дойдёт."""
    handlers = [
        h
        for h in dm.router.message.handlers
        if getattr(h.callback, "__name__", "") == "dm_text"
    ]
    assert len(handlers) == 1
    handler = handlers[0]

    user = User(id=1, is_bot=False, first_name="T")
    chat = Chat(id=1, type="private")
    slash = Message(
        message_id=1,
        date=0,
        chat=chat,
        from_user=user,
        text="/вч_admin_help",
    )
    plain = Message(
        message_id=2,
        date=0,
        chat=chat,
        from_user=user,
        text="статус",
    )

    slash_ok, _ = await handler.check(slash)
    plain_ok, _ = await handler.check(plain)
    assert slash_ok is False
    assert plain_ok is True
