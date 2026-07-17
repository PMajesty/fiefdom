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


def test_open_estate_kb():
    from app.handlers.shared import open_estate_kb

    kb = open_estate_kb("FiefdomBot", 42)
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "Открыть усадьбу"
    assert btn.url == "https://t.me/FiefdomBot?start=realm_42"


def test_choose_primary_cta_onboard_claim():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        9,
        actions=1,
        onboard_step=2,
        goods=30,
        tile_count=1,
        next_claim_cost=30,
    )
    assert label == "Квест: занять землю"
    assert cb == "clm:9"


def test_choose_primary_cta_onboard_build():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        9, actions=1, onboard_step=3, goods=50, min_build_cost=20
    )
    assert label == "Квест: строить"
    assert cb == "bld:9"


def test_choose_primary_cta_onboard_unaffordable_goes_caravan():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        9,
        actions=1,
        onboard_step=2,
        goods=20,
        tile_count=1,
        min_build_cost=50,
        next_claim_cost=30,
    )
    assert label == "Караван"
    assert cb == "snd:9"

    label, cb = choose_primary_cta(
        9,
        actions=1,
        onboard_step=3,
        goods=10,
        tile_count=2,
        min_build_cost=20,
        next_claim_cost=60,
    )
    assert label == "Караван"
    assert cb == "snd:9"


def test_choose_primary_cta_expand_land():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        3, actions=1, onboard_step=4, tile_count=2, goods=0, might=0
    )
    assert label == "Занять землю"
    assert cb == "clm:3"


def test_choose_primary_cta_build_when_goods():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        3, actions=1, onboard_step=4, tile_count=4, goods=25, might=10
    )
    assert label == "Строить"
    assert cb == "bld:3"


def test_choose_primary_cta_raid_when_might():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        3,
        actions=1,
        onboard_step=4,
        tile_count=4,
        goods=5,
        might=8,
        day_number=3,
        min_build_cost=50,
    )
    assert label == "Набег"
    assert cb == "rad:3"


def test_choose_primary_cta_no_raid_while_onboard():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        3,
        actions=1,
        onboard_step=2,
        tile_count=4,
        goods=5,
        might=8,
        day_number=10,
        min_build_cost=50,
        next_claim_cost=120,
    )
    assert label == "Караван"
    assert cb == "snd:3"


def test_choose_primary_cta_no_raid_before_unlock_day():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(
        3,
        actions=1,
        onboard_step=4,
        tile_count=4,
        goods=5,
        might=8,
        day_number=2,
    )
    assert label == "Занять землю"
    assert cb == "clm:3"
    assert label != "Набег"


def test_raid_pact_unlock_helpers():
    from app import balance as B
    from app.engine import (
        raid_pact_lock_hint,
        raid_pact_lock_message,
        raid_pact_unlocked,
    )

    assert raid_pact_unlocked(onboard_step=4, day_number=B.RAID_PACT_UNLOCK_DAY)
    assert not raid_pact_unlocked(onboard_step=3, day_number=10)
    assert not raid_pact_unlocked(onboard_step=4, day_number=2)
    assert raid_pact_lock_hint(onboard_step=2, day_number=10) == "после квестов"
    assert raid_pact_lock_hint(onboard_step=4, day_number=1) == f"с дня {B.RAID_PACT_UNLOCK_DAY}"
    assert raid_pact_lock_hint(onboard_step=4, day_number=3) is None
    assert "после квестов" in raid_pact_lock_message(onboard_step=2, day_number=5)
    assert f"с дня {B.RAID_PACT_UNLOCK_DAY}" in raid_pact_lock_message(
        onboard_step=4, day_number=1
    )


def test_estate_hub_kb_locked_raid():
    from app.handlers.shared import estate_hub_kb

    kb = estate_hub_kb(9, raid_pact_open=False, lock_hint="после квестов")
    by_data = {
        btn.callback_data: btn.text
        for row in kb.inline_keyboard
        for btn in row
    }
    assert by_data["lock:rad:9"] == "Набег - после квестов"
    assert "rad:9" not in by_data
    assert "pct:9" not in by_data
    assert "lock:pct:9" not in by_data


def test_valley_hub_kb_locked_pact():
    from app.handlers.shared import valley_hub_kb

    kb = valley_hub_kb(9, raid_pact_open=False, lock_hint="после квестов")
    by_data = {
        btn.callback_data: btn.text
        for row in kb.inline_keyboard
        for btn in row
    }
    assert by_data["lock:pct:9"] == "Пакт - после квестов"
    assert "pct:9" not in by_data
    assert "rad:9" not in by_data


def test_estate_hub_kb_unlocked_raid():
    from app.handlers.shared import estate_hub_kb

    kb = estate_hub_kb(9, raid_pact_open=True)
    by_data = {
        btn.callback_data: btn.text
        for row in kb.inline_keyboard
        for btn in row
    }
    assert by_data["rad:9"] == "Набег (атака)"
    assert "lock:rad:9" not in by_data


def test_valley_hub_kb_unlocked_pact():
    from app.handlers.shared import valley_hub_kb

    kb = valley_hub_kb(9, raid_pact_open=True)
    by_data = {
        btn.callback_data: btn.text
        for row in kb.inline_keyboard
        for btn in row
    }
    assert by_data["pct:9"] == "Пакт (союз)"
    assert by_data["rum:9"] == "Слухи"
    assert "lock:pct:9" not in by_data


def test_choose_primary_cta_no_actions_caravan():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(7, actions=0, onboard_step=4, tile_count=5)
    assert label == "Караван"
    assert cb == "snd:7"


def test_choose_primary_cta_onboard_ignored_without_actions():
    from app.handlers.shared import choose_primary_cta

    label, cb = choose_primary_cta(7, actions=0, onboard_step=2)
    assert label == "Караван"
    assert cb == "snd:7"


def test_home_kb_has_primary_hubs_map_guide():
    from app.handlers.shared import home_kb

    kb = home_kb(9, "Занять землю", "clm:9")
    rows = kb.inline_keyboard
    assert len(rows) == 3
    assert rows[0][0].text == "Занять землю"
    assert rows[0][0].callback_data == "clm:9"
    assert [btn.text for btn in rows[1]] == ["Усадьба (дела)", "Долина (связи)"]
    assert [btn.callback_data for btn in rows[1]] == ["hub:e:9", "hub:v:9"]
    assert [btn.text for btn in rows[2]] == ["Карта (мир)", "Устав (правила)"]
    assert [btn.callback_data for btn in rows[2]] == ["map:9", "gd:9"]


def test_main_menu_kb_compact_without_fief():
    from app.handlers.shared import main_menu_kb

    kb = main_menu_kb(9)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert data == ["st:9", "hub:e:9", "hub:v:9", "map:9", "gd:9"]
    assert len(kb.inline_keyboard) == 3


def test_main_menu_kb_uses_fief_snapshot():
    from app.handlers.shared import main_menu_kb

    fief = {"actions": 1, "onboard_step": 2, "goods": 0, "might": 0}
    kb = main_menu_kb(5, fief=fief, tile_count=1, min_build_cost=50, next_claim_cost=30)
    assert kb.inline_keyboard[0][0].callback_data == "snd:5"
    assert kb.inline_keyboard[0][0].text == "Караван"
    assert kb.inline_keyboard[1][0].callback_data == "hub:e:5"

    rich = {"actions": 1, "onboard_step": 2, "goods": 50, "might": 0}
    kb2 = main_menu_kb(
        5, fief=rich, tile_count=1, min_build_cost=50, next_claim_cost=30
    )
    assert kb2.inline_keyboard[0][0].callback_data == "clm:5"
    assert kb2.inline_keyboard[0][0].text == "Квест: занять землю"

    builder = {"actions": 1, "onboard_step": 3, "goods": 50, "might": 0}
    kb3 = main_menu_kb(
        5, fief=builder, tile_count=2, min_build_cost=20, next_claim_cost=60
    )
    assert kb3.inline_keyboard[0][0].callback_data == "bld:5"
    assert kb3.inline_keyboard[0][0].text == "Квест: строить"


def test_estate_and_valley_hub_prefixes():
    from app.handlers.shared import estate_hub_kb, valley_hub_kb

    estate = estate_hub_kb(9)
    estate_data = [btn.callback_data for row in estate.inline_keyboard for btn in row]
    assert estate_data == [
        "hld:9",
        "clm:9",
        "bld:9",
        "gth:9",
        "pat:9",
        "dml:9",
        "rad:9",
        "prep:9",
        "home:9",
    ]

    valley = valley_hub_kb(9)
    valley_data = [btn.callback_data for row in valley.inline_keyboard for btn in row]
    assert valley_data == ["snd:9", "pct:9", "rum:9", "prep:9", "home:9"]


def test_home_kb_shows_prepared_when_count():
    from app.handlers.shared import home_kb

    kb = home_kb(9, "Строить", "bld:9", prepared_count=2)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "prep:9" in data
    assert kb.inline_keyboard[2][0].text == "Заявки (2)"

    bare = home_kb(9, "Строить", "bld:9", prepared_count=0)
    bare_data = [btn.callback_data for row in bare.inline_keyboard for btn in row]
    assert "prep:9" not in bare_data


def test_prepared_intents_kb_cancel_buttons():
    from unittest.mock import MagicMock

    from app.handlers.shared import prepared_intents_kb

    engine = MagicMock()
    engine.list_prepared_intents.return_value = (
        [
            {
                "id": 11,
                "status": "open",
                "payload": {"victim_id": 2, "might": 5},
            },
            {
                "id": 12,
                "status": "locked",
                "payload": {"victim_id": 3, "might": 7},
            },
        ],
        [
            {
                "id": 21,
                "status": "open",
                "payload": {"receiver_id": 4, "res": "grain", "amt": 10},
            },
        ],
    )
    engine.raid_intent_target_label.side_effect = lambda intent: {
        11: "Ира",
        12: "Оля",
    }[int(intent["id"])]
    engine.caravan_intent_target_label.return_value = "Кирилл"

    kb = prepared_intents_kb(engine, 9)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert data == ["radx:9:11", "cvx:9:21", "home:9"]
    assert "radx:9:12" not in data


def test_more_menu_kb_compat_hub_picker():
    """Старый more_menu_kb остаётся входной точкой в хабы."""
    from app.handlers.shared import more_menu_kb

    kb = more_menu_kb(9)
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert data == ["hub:e:9", "hub:v:9", "home:9"]


def test_bandit_threshold_math():
    import math
    from app import balance as B

    players = 4
    threshold = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
    assert threshold == int(math.ceil(3.0 * 4))


def test_format_claim_button_field_and_wilds():
    from app.handlers.dm import format_claim_button
    from app import balance as B

    assert format_claim_button(0, 2, B.TILE_FIELD, 2) == "А3 Поле · 20 тов."
    assert format_claim_button(0, 2, B.TILE_WILDS, 2) == "А3 Глушь · 40 тов."
    assert (
        format_claim_button(0, 2, B.TILE_WILDS, 2, is_overgrown=True)
        == "А3 Глушь · 20 тов."
    )


def test_format_building_type_and_build_cost_labels():
    from app.handlers.dm import (
        format_building_type_label,
        format_build_cost_label,
        format_build_tile_button,
    )
    from app import balance as B

    assert format_building_type_label(B.BLD_FARM) == "Ферма · 20 тов."
    empty = {"x": 0, "y": 0, "building": None, "building_level": 0, "damaged": False}
    assert format_build_cost_label(B.BLD_FARM, empty) == "20 тов."
    assert format_build_tile_button(B.BLD_FARM, empty) == "А1 · 20 тов."

    upgrade = {
        "x": 1,
        "y": 0,
        "building": B.BLD_FARM,
        "building_level": 1,
        "damaged": False,
    }
    assert format_build_cost_label(B.BLD_FARM, upgrade) == "50 тов."
    assert format_build_tile_button(B.BLD_FARM, upgrade) == "Б1 →2 · 50 тов."
    assert format_building_type_label(B.BLD_FARM, [upgrade]) == "Ферма · 50 тов."
    assert format_building_type_label(B.BLD_FARM, [upgrade, empty]) == "Ферма · 20 тов."

    repair = {
        "x": 2,
        "y": 0,
        "building": B.BLD_WORKSHOP,
        "building_level": 2,
        "damaged": True,
    }
    assert format_build_cost_label(B.BLD_FARM, repair) == "30 тов."
    assert "ремонт" in format_build_tile_button(B.BLD_FARM, repair)

    occupied = {
        "x": 0,
        "y": 1,
        "building": B.BLD_BARN,
        "building_level": 1,
        "damaged": False,
    }
    assert format_build_cost_label(B.BLD_FARM, occupied) == "занято"
    assert format_building_type_label(B.BLD_FARM, [occupied]) == "Ферма"

    maxed = {
        "x": 0,
        "y": 2,
        "building": B.BLD_FARM,
        "building_level": 3,
        "damaged": False,
    }
    assert format_build_cost_label(B.BLD_FARM, maxed) == "макс."
    assert format_building_type_label(B.BLD_FARM, [maxed]) == "Ферма · макс."


def test_building_types_kb_shows_l1_cost():
    from app.handlers.dm import building_types_kb
    from app import balance as B

    empty = {"x": 0, "y": 0, "building": None, "building_level": 0, "damaged": False}
    kb = building_types_kb(5, [empty])
    labels = [row[0].text for row in kb.inline_keyboard[:-1]]
    assert f"Ферма · {B.BUILDING_COSTS[B.BLD_FARM][1]} тов." in labels
    assert all("тов." in t for t in labels)


def test_building_types_kb_shows_upgrade_cost_for_starter_farm():
    from app.handlers.dm import building_types_kb
    from app import balance as B

    farm = {
        "x": 0,
        "y": 0,
        "building": B.BLD_FARM,
        "building_level": 1,
        "damaged": False,
    }
    kb = building_types_kb(5, [farm])
    labels = [row[0].text for row in kb.inline_keyboard[:-1]]
    assert f"Ферма · {B.BUILDING_COSTS[B.BLD_FARM][2]} тов." in labels
    assert f"Мастерская · {B.BUILDING_COSTS[B.BLD_WORKSHOP][1]} тов." not in labels
    assert "Мастерская" in labels


def test_patrol_confirm_callback_shape():
    from app.handlers.dm import (
        patrol_confirm_callback,
        patrol_confirm_kb,
        patrol_confirm_text,
        patrol_prompt_callback,
    )
    from app import balance as B

    assert patrol_prompt_callback(9) == "pat:9"
    assert patrol_confirm_callback(9) == "pat:9:ok"
    text = patrol_confirm_text()
    if B.PATROL_COST_MIGHT > 0:
        assert f"−{B.PATROL_COST_MIGHT} силы" in text
    else:
        assert "силы" not in text
    assert "1 действие" in text
    assert f"на {B.PATROL_TICKS} тик(а)" in text
    assert f"+{B.PATROL_DEFENSE_BONUS} защиты" in text
    assert "защиту от набегов" in text

    kb = patrol_confirm_kb(9)
    texts = [btn.text for btn in kb.inline_keyboard[0]]
    data = [btn.callback_data for btn in kb.inline_keyboard[0]]
    assert texts == ["Подтвердить", "Отмена"]
    assert data == ["pat:9:ok", "st:9"]


def test_pending_cancel_helpers():
    from app.handlers.dm import (
        is_pending_cancel_text,
        pending_cancel_callback,
        pending_cancel_kb,
    )

    assert is_pending_cancel_text("отмена")
    assert is_pending_cancel_text(" Cancel ")
    assert not is_pending_cancel_text("отменить")
    assert pending_cancel_callback(4) == "pend:cancel:4"
    kb = pending_cancel_kb(4)
    assert kb.inline_keyboard[0][0].callback_data == "pend:cancel:4"
    assert kb.inline_keyboard[0][0].text == "Отмена"


def test_claimable_kb_includes_cost_preview():
    from app.handlers.dm import claimable_kb
    from app import balance as B

    kb = claimable_kb(
        3,
        [(0, 2)],
        next_tile_count=2,
        tile_meta={(0, 2): (B.TILE_FIELD, False)},
    )
    btn = kb.inline_keyboard[0][0]
    assert btn.text == "А3 Поле · 20 тов."
    assert btn.callback_data == "clm:3:0:2"


def test_raid_targets_kb_hides_exact_might():
    from app.handlers.dm import raid_targets_kb

    kb = raid_targets_kb(
        1,
        [
            {"id": 2, "name": "Сосед", "might": 17, "realm_id": 1},
            {"id": 3, "name": "Слабый", "might": 2, "realm_id": 1},
        ],
    )
    texts = [row[0].text for row in kb.inline_keyboard[:-1]]
    assert texts == ["Сосед · крепкая", "Слабый · тонкая"]
    assert all("сила" not in t and "17" not in t and "2" not in t for t in texts)


def test_announce_formatters_escape_names():
    from app import balance as B
    from app.handlers.shared import (
        format_join_announce,
        format_pact_create_announce,
        format_pact_join_announce,
        format_pact_leave_announce,
        format_raid_announce,
        format_send_announce,
    )

    assert format_join_announce("Усадьба A <B>") == (
        "🏡 В долине новая усадьба: Усадьба A &lt;B&gt;"
    )
    assert format_raid_announce("Набег X на Y") == "⚔️ Набег X на Y"
    assert format_send_announce(
        "Усадьба <X>", "Двор <Y>", 10, B.RES_GRAIN
    ) == (
        "📦 Усадьба &lt;X&gt; отправила обоз: "
        "10 Зерно → Двор &lt;Y&gt;"
    )
    assert format_pact_create_announce("Основатель", "Север") == (
        '🤝 Новый пакт "Север" (Основатель)'
    )
    assert format_pact_join_announce("Новичок", "Север") == (
        '🤝 Новичок в пакте "Север"'
    )
    assert format_pact_leave_announce("Беглец", "Север") == (
        '🤝 Беглец больше не в пакте "Север"'
    )
    assert format_pact_leave_announce("Беглец", "Север", dissolved=True) == (
        '🤝 Беглец больше не в пакте - "Север" распущен'
    )
    for text in (
        format_join_announce("A"),
        format_raid_announce("B"),
        format_send_announce("C", "D", 1, B.RES_GRAIN),
        format_pact_leave_announce("D", "E", dissolved=True),
    ):
        assert "\u2014" not in text
        assert "\u00ab" not in text
        assert "\u00bb" not in text


async def test_announce_realm_posts_to_player_dms(monkeypatch):
    from app.handlers import shared as shared_mod

    sent: list[tuple[int, str]] = []

    class _Db:
        def list_fiefs(self, realm_id):
            assert realm_id == 7
            return [
                {"id": 1, "user_id": 101},
                {"id": 2, "user_id": 202},
            ]

    class _Engine:
        db = _Db()

    class _Bot:
        pass

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _fake_send_game)

    await shared_mod.announce_realm(_Bot(), 7, "🏡 тест")
    assert sent == [(101, "🏡 тест"), (202, "🏡 тест")]


async def test_post_realm_public_posts_to_group_chat(monkeypatch):
    from app.handlers import shared as shared_mod

    sent: list[tuple[int, str, object]] = []

    class _Db:
        def get_realm(self, realm_id):
            assert realm_id == 7
            return {"id": 7, "chat_id": -100500}

    class _Engine:
        db = _Db()

    async def _fake_send_game(bot, chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs.get("reply_markup")))
        return True

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _fake_send_game)

    kb = object()
    ok = await shared_mod.post_realm_public(object(), 7, "⚔️ набег", reply_markup=kb)
    assert ok is True
    assert sent == [(-100500, "⚔️ набег", kb)]


async def test_post_realm_public_skips_missing_or_zero_realm(monkeypatch):
    from app.handlers import shared as shared_mod

    called = False

    class _Db:
        def get_realm(self, realm_id):
            return None

    class _Engine:
        db = _Db()

    async def _fake_send_game(*_a, **_k):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _fake_send_game)

    assert await shared_mod.post_realm_public(object(), 0, "текст") is False
    assert await shared_mod.post_realm_public(object(), 9, "текст") is False
    assert called is False


async def test_post_realm_public_swallows_send_errors(monkeypatch):
    from app.handlers import shared as shared_mod

    class _Db:
        def get_realm(self, realm_id):
            return {"id": 1, "chat_id": -1}

    class _Engine:
        db = _Db()

    async def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _boom)

    assert await shared_mod.post_realm_public(object(), 1, "текст") is False


async def test_announce_realm_skips_empty_realm(monkeypatch):
    from app.handlers import shared as shared_mod

    called = False

    class _Db:
        def list_fiefs(self, realm_id):
            return []

    class _Engine:
        db = _Db()

    async def _fake_send_game(*_a, **_k):
        nonlocal called
        called = True

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _fake_send_game)

    await shared_mod.announce_realm(object(), 1, "текст")
    assert called is False


async def test_announce_realm_swallows_send_errors(monkeypatch):
    from app.handlers import shared as shared_mod

    class _Db:
        def list_fiefs(self, realm_id):
            return [{"id": 1, "user_id": 9}]

    class _Engine:
        db = _Db()

    async def _boom(*_a, **_k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "send_game", _boom)

    await shared_mod.announce_realm(object(), 1, "текст")


async def test_announce_continent_fans_out_to_adjacent(monkeypatch):
    from app.handlers import shared as shared_mod

    announced: list[int] = []

    class _Db:
        def list_adjacent_realms(self, realm_id):
            assert realm_id == 1
            return [{"id": 2}, {"id": 3}, {"id": 2}]

    class _Engine:
        db = _Db()

    async def _fake_announce_realm(bot, realm_id, text, *, reply_markup=None):
        announced.append(int(realm_id))

    monkeypatch.setattr(shared_mod, "get_engine", lambda: _Engine())
    monkeypatch.setattr(shared_mod, "announce_realm", _fake_announce_realm)

    await shared_mod.announce_continent(object(), 1, "🛒 лот")
    assert announced == [1, 2, 3]
