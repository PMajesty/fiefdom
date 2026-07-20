"""Тесты notifier: deep-link и публикация в групповые чаты долин."""
from __future__ import annotations


def test_deep_link_url():
    from app.notifier import deep_link_url

    assert deep_link_url("MyBot", "join_1") == "https://t.me/MyBot?start=join_1"


def test_open_estate_kb():
    from app.notifier import open_estate_kb

    kb = open_estate_kb("FiefdomBot", 42)
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "Открыть усадьбу"
    assert btn.url == "https://t.me/FiefdomBot?start=realm_42"


async def test_post_realm_public_posts_to_group_chat(monkeypatch):
    from app import notifier as notifier_mod

    sent: list[tuple[int, str, object]] = []

    class _Engine:
        def get_realm(self, realm_id):
            assert realm_id == 7
            return {"id": 7, "chat_id": -100500}

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    kb = object()
    ok = await notifier_mod.post_realm_public(
        object(), 7, "⚔️ набег", reply_markup=kb
    )
    assert ok is True
    assert sent == [(-100500, "⚔️ набег", kb)]


async def test_post_realm_public_skips_missing_or_zero_realm(monkeypatch):
    from app import notifier as notifier_mod

    called = False

    class _Engine:
        def get_realm(self, realm_id):
            return None

    async def _fake_send_game(*_a, **_k):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    assert await notifier_mod.post_realm_public(object(), 0, "текст") is False
    assert await notifier_mod.post_realm_public(object(), 9, "текст") is False
    assert called is False


async def test_post_realm_public_swallows_send_errors(monkeypatch):
    from app import notifier as notifier_mod

    class _Engine:
        def get_realm(self, realm_id):
            return {"id": 1, "chat_id": -1}

    async def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _boom)

    assert await notifier_mod.post_realm_public(object(), 1, "текст") is False
