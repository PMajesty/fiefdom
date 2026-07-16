"""Тесты хелперов хендлеров (без Telegram/БД)."""
from __future__ import annotations

import os

# до импорта app.config
os.environ.setdefault("ADMIN_USER_ID", "42")


def test_parse_start_payload():
    from app.handlers.shared import parse_start_payload

    assert parse_start_payload(None) == (None, None)
    assert parse_start_payload("") == (None, None)
    assert parse_start_payload("join_7") == ("join", 7)
    assert parse_start_payload("realm_12") == ("realm", 12)
    assert parse_start_payload("JOIN_3") == ("join", 3)
    assert parse_start_payload("other") == (None, None)


def test_is_admin_respects_env(monkeypatch):
    monkeypatch.setattr("app.handlers.shared.ADMIN_USER_ID", 42)
    from app.handlers.shared import is_admin

    assert is_admin(42) is True
    assert is_admin(1) is False
    assert is_admin(None) is False


def test_deep_link_url():
    from app.handlers.shared import deep_link_url

    assert deep_link_url("MyBot", "join_1") == "https://t.me/MyBot?start=join_1"


def test_parse_trade_line():
    from app.handlers.dm import _parse_trade_line
    from app import balance as B

    assert _parse_trade_line("зерно 10 товары 5") == (
        B.RES_GRAIN,
        10,
        B.RES_GOODS,
        5,
    )
    assert _parse_trade_line("goods 3 grain 8") == (
        B.RES_GOODS,
        3,
        B.RES_GRAIN,
        8,
    )
    assert _parse_trade_line("nonsense") is None


def test_main_menu_kb_prefixes():
    from app.handlers.shared import main_menu_kb

    kb = main_menu_kb(9)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "st:9" in data
    assert "map:9" in data
    assert "mkt:9" in data
    assert "clm:9" in data
    assert "bld:9" in data
    assert "pat:9" in data
    assert "rad:9" in data
    assert "trd:9" in data
    assert "pct:9" in data


def test_bandit_threshold_math():
    import math
    from app import balance as B

    players = 4
    threshold = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
    assert threshold == 10
