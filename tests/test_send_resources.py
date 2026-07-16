"""Прямая передача ресурсов на доверии."""
from __future__ import annotations

import os
from contextlib import nullcontext
from unittest.mock import MagicMock

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.engine import Engine
from app.handlers.dm import _parse_send_line
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

    def get_fief(fid):
        return {1: dict(sender), 2: dict(receiver)}.get(fid)

    db.get_fief.side_effect = get_fief

    def update_fief(fid, **fields):
        target = sender if fid == 1 else receiver
        target.update(fields)

    db.update_fief.side_effect = update_fief

    engine = Engine(db)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=barn)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    return engine, sender, receiver


def test_send_resources_grain_ok():
    engine, sender, receiver = _engine_pair()
    msg = engine.send_resources(1, 2, B.RES_GRAIN, 10)
    assert sender["grain"] == 40
    assert receiver["grain"] == 15
    assert "10" in msg
    assert "Бета" in msg


def test_send_resources_rejects_might():
    engine, _, _ = _engine_pair()
    try:
        engine.send_resources(1, 2, B.RES_MIGHT, 5)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "зерно" in str(exc).lower() or "товар" in str(exc).lower()


def test_send_resources_rejects_self():
    engine, _, _ = _engine_pair()
    try:
        engine.send_resources(1, 1, B.RES_GRAIN, 5)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "себе" in str(exc).lower()


def test_send_resources_rejects_insufficient():
    engine, _, _ = _engine_pair(grain_from=3)
    try:
        engine.send_resources(1, 2, B.RES_GRAIN, 10)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "недостат" in str(exc).lower()


def test_send_resources_rejects_full_stash():
    engine, _, _ = _engine_pair(grain_to=B.stash_cap(0))
    try:
        engine.send_resources(1, 2, B.RES_GRAIN, 1)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "склад" in str(exc).lower()
