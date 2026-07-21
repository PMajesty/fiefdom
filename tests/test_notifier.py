"""Тесты notifier: deep-link и fan-out публичных извещений в лички."""
from __future__ import annotations

from app.notifier import FanoutResult


def test_deep_link_url():
    from app.notifier import deep_link_url

    assert deep_link_url("MyBot", "join_1") == "https://t.me/MyBot?start=join_1"


def test_open_estate_kb():
    from app.notifier import open_estate_kb

    kb = open_estate_kb("FiefdomBot", 42)
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "Открыть усадьбу"
    assert btn.url == "https://t.me/FiefdomBot?start=realm_42"


async def test_post_realm_public_fans_out_to_player_dms(monkeypatch):
    from app import notifier as notifier_mod

    sent: list[tuple[int, str, object]] = []

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            assert realm_id == 7
            return [
                {"id": 1, "user_id": 101},
                {"id": 2, "user_id": 202},
            ]

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    kb = object()
    result = await notifier_mod.post_realm_public(
        object(), 7, "⚔️ набег", reply_markup=kb
    )
    assert result.ok is True
    assert result.targets == 2
    assert result.sent == 2
    assert sent == [
        (101, "⚔️ набег", kb),
        (202, "⚔️ набег", kb),
    ]


async def test_post_realm_public_skips_missing_or_zero_realm(monkeypatch):
    from app import notifier as notifier_mod

    called = False

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            raise AssertionError("should not query fiefs for empty realm_id")

    async def _fake_send_game(*_a, **_k):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    result = await notifier_mod.post_realm_public(object(), 0, "текст")
    assert result.ok is False
    assert result.targets == 0
    assert called is False


async def test_post_realm_public_empty_realm_is_ok(monkeypatch):
    from app import notifier as notifier_mod

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            return []

    async def _fake_send_game(*_a, **_k):
        raise AssertionError("no recipients")

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    result = await notifier_mod.post_realm_public(object(), 9, "текст")
    assert result.ok is True
    assert result.targets == 0


async def test_post_realm_public_partial_failure_is_false(monkeypatch):
    from app import notifier as notifier_mod

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            return [
                {"id": 1, "user_id": 101},
                {"id": 2, "user_id": 202},
            ]

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        return chat_id == 101

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    result = await notifier_mod.post_realm_public(object(), 1, "текст")
    assert result.ok is False
    assert result.targets == 2
    assert result.sent == 1


async def test_post_realm_public_swallows_send_errors(monkeypatch):
    from app import notifier as notifier_mod

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            return [{"id": 1, "user_id": 9}]

    async def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _boom)

    result = await notifier_mod.post_realm_public(object(), 1, "текст")
    assert result.ok is False
    assert result.targets == 1


async def test_post_continent_public_requires_all_realms(monkeypatch):
    from app import notifier as notifier_mod

    posted: list[int] = []

    class _Engine:
        def adjacent_realm_ids(self, realm_id):
            assert realm_id == 1
            return [2, 3, 2]

    async def _fake_post_realm(bot, realm_id, text, *, reply_markup=None):
        posted.append(int(realm_id))
        if realm_id == 3:
            return FanoutResult(ok=False, targets=2)
        return FanoutResult(ok=True, targets=1)

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "post_realm_public", _fake_post_realm)

    ok = await notifier_mod.post_continent_public(object(), 1, "🛒 обоз")
    assert ok is False
    assert posted == [1, 2, 3]


def test_rumor_fanout_should_ack_partial_and_empty():
    from app.notifier import FanoutResult, rumor_fanout_should_ack

    assert rumor_fanout_should_ack(FanoutResult(ok=True, targets=0, sent=0))
    assert rumor_fanout_should_ack(FanoutResult(ok=True, targets=2, sent=2))
    assert rumor_fanout_should_ack(FanoutResult(ok=False, targets=2, sent=1))
    assert not rumor_fanout_should_ack(
        FanoutResult(ok=False, targets=2, sent=0)
    )
    assert not rumor_fanout_should_ack(
        FanoutResult(ok=False, targets=0, sent=0)
    )


async def test_post_digest_fans_out_with_open_estate_kb(monkeypatch):
    from app import notifier as notifier_mod

    sent: list[tuple[int, str, object]] = []

    class _Engine:
        def fiefs_of_realm(self, realm_id):
            assert realm_id == 5
            return [{"id": 1, "user_id": 77}]

    class _Bot:
        async def get_me(self):
            return type("Me", (), {"username": "FiefBot"})()

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(notifier_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(notifier_mod, "send_game", _fake_send_game)

    await notifier_mod.post_digest(_Bot(), 5, "📜 сводка")
    assert len(sent) == 1
    assert sent[0][0] == 77
    assert sent[0][1] == "📜 сводка"
    assert sent[0][2] is not None
    assert sent[0][2].inline_keyboard[0][0].url.endswith("realm_5")
