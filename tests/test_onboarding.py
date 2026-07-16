"""Онбординг: шаги 1→2(клейм)→3(стройка)→4, награды, громкий квест в статусе."""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.engine import (
    Engine,
    fief_name_for_user,
    onboard_quest_html,
    try_complete_onboard_build,
    try_complete_onboard_claim,
)


class _FakeDB:
    """Минимальное хранилище усадеб для тестов Engine._onboard_*."""

    def __init__(self, fiefs: dict[int, dict]):
        self.fiefs = {fid: dict(f) for fid, f in fiefs.items()}

    def get_fief(self, fief_id: int) -> dict | None:
        row = self.fiefs.get(fief_id)
        return dict(row) if row else None

    def update_fief(self, fief_id: int, **fields) -> None:
        self.fiefs[fief_id].update(fields)


def test_onboard_quest_html_loud_for_steps_2_and_3():
    q2 = onboard_quest_html(2)
    q3 = onboard_quest_html(3)
    assert q2 is not None and q2.startswith("<b>Квест:") and q2.endswith("</b>")
    assert "клетк" in q2
    assert str(B.ONBOARD_DAY2_GOODS) in q2
    assert q3 is not None and q3.startswith("<b>Квест:") and q3.endswith("</b>")
    assert "здани" in q3
    assert str(B.ONBOARD_DAY3_GRAIN) in q3
    assert onboard_quest_html(1) is None
    assert onboard_quest_html(4) is None


def test_try_complete_onboard_claim_advances_with_reward():
    patch = try_complete_onboard_claim({"onboard_step": 2, "goods": 10})
    assert patch == {"onboard_step": 3, "goods": 10 + B.ONBOARD_DAY2_GOODS}
    assert try_complete_onboard_claim({"onboard_step": 3, "goods": 10}) is None
    assert try_complete_onboard_claim({"onboard_step": 1, "goods": 10}) is None


def test_try_complete_onboard_build_advances_with_reward():
    patch = try_complete_onboard_build({"onboard_step": 3, "grain": 20})
    assert patch == {"onboard_step": 4, "grain": 20 + B.ONBOARD_DAY3_GRAIN}
    assert try_complete_onboard_build({"onboard_step": 4, "grain": 20}) is None
    assert try_complete_onboard_build({"onboard_step": 2, "grain": 20}) is None


def test_fief_name_for_user_prefers_username():
    user = SimpleNamespace(
        id=1, full_name="Артём Иванов", first_name="Артём", username="artem_x"
    )
    assert fief_name_for_user(user) == "Усадьба @artem_x"


def test_fief_name_for_user_falls_back_to_full_name():
    user = SimpleNamespace(
        id=1, full_name="Артём Иванов", first_name="Артём", username=None
    )
    assert fief_name_for_user(user) == "Усадьба Артём Иванов"


def test_fief_name_for_user_accepts_db_row():
    assert fief_name_for_user({"username": "artem_x", "display_name": "Артём"}) == (
        "Усадьба @artem_x"
    )
    assert fief_name_for_user({"username": None, "display_name": "Артём Иванов"}) == (
        "Усадьба Артём Иванов"
    )


def test_fief_label_uses_profile_and_syncs():
    db = MagicMock()
    db.get_user.return_value = {"username": "artem_x", "display_name": "Артём"}
    engine = Engine(db)
    fief = {"id": 7, "user_id": 100, "name": "Усадьба Артём"}

    assert engine.fief_label(fief) == "Усадьба @artem_x"
    db.update_fief.assert_called_once_with(7, name="Усадьба @artem_x")


def test_join_fief_sets_onboard_step_2():
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db._fetchone.return_value = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_FIELD,
        "owner_fief_id": None,
    }
    db.get_realm.return_value = {"width": 6, "height": 6}
    db.get_tiles.return_value = [
        {"id": 50, "x": 1, "y": 2, "tile_type": B.TILE_FIELD, "owner_fief_id": None},
    ]
    db.create_fief.return_value = {"id": 7, "name": "Усадьба @ivan"}
    engine = Engine(db)
    engine.maybe_grow_map = MagicMock(return_value=None)  # type: ignore[method-assign]
    user = SimpleNamespace(id=100, full_name="Иван Тестов", first_name="Иван", username="ivan")

    fief, msg = engine.join_fief(1, user, tile_id=50)

    assert fief["id"] == 7
    assert db.create_fief.call_args.kwargs["onboard_step"] == 2
    assert db.create_fief.call_args.args[0] == 1
    assert db.create_fief.call_args.args[1] == 100
    assert db.create_fief.call_args.args[2] == "Усадьба @ivan"
    assert "Усадьба @ivan" in msg
    assert "Урожай собирается сам" in msg
    assert "занять" in msg.lower() or "соседн" in msg
    assert str(B.CLAIM_COSTS[2]) in msg
    db.set_fief_names_for_user.assert_called_with(100, "Усадьба @ivan")


def test_join_fief_rejects_ruins_tile():
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db._fetchone.return_value = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_RUINS,
        "owner_fief_id": None,
    }
    engine = Engine(db)
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    with pytest.raises(ValueError, match="Нельзя начать здесь"):
        engine.join_fief(1, user, tile_id=50)
    db.create_fief.assert_not_called()


def test_join_fief_rejects_tile_adjacent_to_ruins():
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db._fetchone.return_value = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_FIELD,
        "owner_fief_id": None,
    }
    db.get_realm.return_value = {"width": 6, "height": 6}
    db.get_tiles.return_value = [
        {"id": 50, "x": 1, "y": 2, "tile_type": B.TILE_FIELD, "owner_fief_id": None},
        {"id": 51, "x": 2, "y": 2, "tile_type": B.TILE_RUINS, "owner_fief_id": None},
    ]
    engine = Engine(db)
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    with pytest.raises(ValueError, match="Нельзя начать здесь"):
        engine.join_fief(1, user, tile_id=50)
    db.create_fief.assert_not_called()


def test_starter_tile_choices_excludes_ruins_and_neighbors():
    db = MagicMock()
    db.get_realm.return_value = {"width": 5, "height": 5}
    # Руины в (2,2): блок dist 0 и ortho-соседи (1,2)/(3,2)/(2,1)/(2,3)
    tiles = []
    tid = 0
    for y in range(5):
        for x in range(5):
            tiles.append(
                {
                    "id": tid,
                    "x": x,
                    "y": y,
                    "tile_type": B.TILE_RUINS if (x, y) == (2, 2) else B.TILE_FIELD,
                    "owner_fief_id": None,
                    "is_core": False,
                    "is_overgrown": False,
                }
            )
            tid += 1
    db.get_tiles.return_value = tiles
    engine = Engine(db)

    picked = engine.starter_tile_choices(1, count=3)
    coords = {(p["x"], p["y"]) for p in picked}
    blocked = {(2, 2), (1, 2), (3, 2), (2, 1), (2, 3)}
    assert coords.isdisjoint(blocked)
    assert len(picked) == 3


def test_onboard_patience_hint_when_unaffordable():
    from app.engine import onboard_patience_hint

    hint = onboard_patience_hint(
        onboard_step=2,
        goods=20,
        tile_count=1,
        min_build_cost=50,
    )
    assert hint is not None
    assert "рынок" in hint
    assert "30" in hint
    assert (
        onboard_patience_hint(
            onboard_step=2, goods=30, tile_count=1, min_build_cost=50
        )
        is None
    )
    build_hint = onboard_patience_hint(
        onboard_step=3, goods=15, tile_count=2, min_build_cost=20
    )
    assert build_hint is not None
    assert "20" in build_hint
    assert (
        onboard_patience_hint(
            onboard_step=3, goods=20, tile_count=2, min_build_cost=20
        )
        is None
    )
    assert (
        onboard_patience_hint(
            onboard_step=4, goods=20, tile_count=1, min_build_cost=50
        )
        is None
    )


def test_onboard_claim_engine_advances_step_and_goods():
    db = _FakeDB({1: {"id": 1, "onboard_step": 2, "goods": 5, "grain": 30}})
    engine = Engine(db)
    engine._onboard_claim(1)
    assert db.fiefs[1]["onboard_step"] == 3
    assert db.fiefs[1]["goods"] == 5 + B.ONBOARD_DAY2_GOODS
    engine._onboard_claim(1)
    assert db.fiefs[1]["onboard_step"] == 3
    assert db.fiefs[1]["goods"] == 5 + B.ONBOARD_DAY2_GOODS


def test_onboard_build_engine_advances_step_and_grain():
    db = _FakeDB({1: {"id": 1, "onboard_step": 3, "goods": 5, "grain": 12}})
    engine = Engine(db)
    engine._onboard_build(1)
    assert db.fiefs[1]["onboard_step"] == 4
    assert db.fiefs[1]["grain"] == 12 + B.ONBOARD_DAY3_GRAIN
    engine._onboard_build(1)
    assert db.fiefs[1]["onboard_step"] == 4
    assert db.fiefs[1]["grain"] == 12 + B.ONBOARD_DAY3_GRAIN


def test_post_trade_does_not_advance_onboard():
    state = {
        1: {
            "id": 1,
            "realm_id": 9,
            "grain": 40,
            "goods": 20,
            "onboard_step": 3,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        }
    }
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_fief.side_effect = lambda fid: dict(state[fid])
    db.create_trade.return_value = {"id": 99}

    def _update(fid, **fields):
        state[fid].update(fields)

    db.update_fief.side_effect = _update
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]

    msg = engine.post_trade(1, B.RES_GRAIN, 10, B.RES_GOODS, 5)
    assert "Лот #99" in msg
    assert state[1]["onboard_step"] == 3
    assert state[1]["grain"] == 30


def test_accept_trade_does_not_advance_onboard():
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    state = {
        1: {
            "id": 1,
            "realm_id": 9,
            "grain": 50,
            "goods": 20,
            "onboard_step": 3,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
        2: {
            "id": 2,
            "realm_id": 9,
            "grain": 30,
            "goods": 10,
            "onboard_step": 3,
            "pending_grain": 0,
            "pending_goods": 0,
            "pending_might": 0,
            "might": 5,
        },
    }
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    trade = {
        "id": 5,
        "status": "open",
        "expires_at": expires,
        "offerer_fief_id": 2,
        "target_fief_id": None,
        "realm_id": 9,
        "give_res": B.RES_GOODS,
        "give_amt": 5,
        "want_res": B.RES_GRAIN,
        "want_amt": 10,
    }
    db.get_trade.return_value = trade
    db.claim_open_trade.return_value = {**trade, "status": "done"}
    db.get_fief.side_effect = lambda fid: dict(state[fid])
    db.get_realm.return_value = {
        "id": 9,
        "active_minor_key": None,
        "active_minor_until": None,
    }

    def _update(fid, **fields):
        state[fid].update(fields)

    db.update_fief.side_effect = _update
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]

    engine.accept_trade(1, 5)

    assert state[1]["onboard_step"] == 3
    assert state[2]["onboard_step"] == 3
    assert state[2]["grain"] == 40


def test_status_card_puts_bold_quest_after_title():
    db = MagicMock()
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(grain=5.0, goods=1.0, might=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    db.get_fief.return_value = {
        "id": 1,
        "name": "Усадьба А",
        "realm_id": 3,
        "grain": 30,
        "goods": 20,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 2,
        "last_active_at": datetime.now(timezone.utc),
        "patrol_until": None,
        "shield_until": None,
    }
    db.get_realm.return_value = {"id": 3, "day_number": 4}
    db.fief_tiles.return_value = [
        {
            "is_overgrown": False,
            "building": B.BLD_FARM,
            "building_level": 1,
            "damaged": False,
        }
    ]

    text = engine.status_card(1)
    lines = text.split("\n")
    assert lines[0].startswith("🏡 <b>Усадьба А</b>")
    assert lines[1] == onboard_quest_html(2)
    assert "<b>Квест:" in lines[1]
    assert "рынок" in lines[2]
    assert "30" in lines[2]
    assert not lines[-1].startswith("<b>Квест:")


def test_status_card_advances_claim_quest_if_already_expanded():
    state = {
        "id": 1,
        "name": "Усадьба А",
        "realm_id": 3,
        "grain": 30,
        "goods": 10,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 2,
        "last_active_at": datetime.now(timezone.utc),
        "patrol_until": None,
        "shield_until": None,
    }
    db = MagicMock()

    def _get_fief(_fid):
        return dict(state)

    def _update(_fid, **fields):
        state.update(fields)

    db.get_fief.side_effect = _get_fief
    db.update_fief.side_effect = _update
    db.get_realm.return_value = {"id": 3, "day_number": 4}
    db.fief_tiles.return_value = [
        {"is_overgrown": False, "building": B.BLD_FARM, "building_level": 1},
        {"is_overgrown": False, "building": None, "building_level": 0},
    ]
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(grain=5.0, goods=1.0, might=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]

    text = engine.status_card(1)
    assert state["onboard_step"] == 3
    assert state["goods"] == 10 + B.ONBOARD_DAY2_GOODS
    assert onboard_quest_html(3) in text.split("\n")


def test_status_card_bumps_stuck_step_1_to_2():
    state = {
        "id": 1,
        "name": "Усадьба Б",
        "realm_id": 3,
        "grain": 30,
        "goods": 20,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 1,
        "last_active_at": datetime.now(timezone.utc),
        "patrol_until": None,
        "shield_until": None,
    }
    db = MagicMock()

    def _get_fief(_fid):
        return dict(state)

    def _update(_fid, **fields):
        state.update(fields)

    db.get_fief.side_effect = _get_fief
    db.update_fief.side_effect = _update
    db.get_realm.return_value = {"id": 3, "day_number": 2}
    db.fief_tiles.return_value = [{"is_overgrown": False}]
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(grain=5.0, goods=1.0, might=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]

    text = engine.status_card(1)
    assert state["onboard_step"] == 2
    assert onboard_quest_html(2) in text.split("\n")


def test_status_card_mentions_raid_pact_unlock_after_quests():
    from app import balance as B

    db = MagicMock()
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(grain=5.0, goods=1.0, might=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    db.get_fief.return_value = {
        "id": 1,
        "name": "Усадьба В",
        "realm_id": 3,
        "grain": 30,
        "goods": 20,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 4,
        "last_active_at": datetime.now(timezone.utc),
        "patrol_until": None,
        "shield_until": None,
    }
    db.get_realm.return_value = {"id": 3, "day_number": 2}
    db.fief_tiles.return_value = [{"is_overgrown": False}]

    text = engine.status_card(1)
    assert f"Набег и пакт - с дня {B.RAID_PACT_UNLOCK_DAY}." in text
    assert onboard_quest_html(4) is None


def test_status_card_shows_next_tick():
    db = MagicMock()
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(grain=5.0, goods=1.0, might=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    db.get_fief.return_value = {
        "id": 1,
        "name": "Усадьба Г",
        "realm_id": 3,
        "grain": 30,
        "goods": 20,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 4,
        "last_active_at": datetime.now(timezone.utc),
        "patrol_until": None,
        "shield_until": None,
    }
    db.get_realm.return_value = {
        "id": 3,
        "day_number": 5,
        "timezone": "Europe/Moscow",
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 0,
    }
    db.fief_tiles.return_value = [{"is_overgrown": False}]
    fixed_now = datetime(2026, 7, 16, 15, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    with patch("app.engine.datetime", _FrozenDateTime):
        text = engine.status_card(1)

    assert "Следующий тик: 16.07 19:00" in text


def test_guide_mentions_raid_pact_unlock_day():
    from app import balance as B
    from app.domain.guide import game_guide

    text = game_guide()
    assert f"с дня {B.RAID_PACT_UNLOCK_DAY}" in text
    assert "после квестов" in text
    assert "квест" in text.lower()


def test_guide_explains_patrol():
    from app import balance as B
    from app.domain.guide import game_guide

    text = game_guide()
    assert "<b>Дозор</b>" in text
    assert f"{B.PATROL_COST_MIGHT} силы" in text
    assert f"+{B.PATROL_DEFENSE_BONUS} к защите" in text
    assert f"на {B.PATROL_HOURS}ч" in text
    assert "сторожка даёт защиту постоянно" in text
    assert "тумане" in text and "дозор почти бесполезен" in text


def test_guide_explains_core_systems():
    from app import balance as B
    from app.domain.guide import game_guide

    text = game_guide()
    assert "Ферма" in text and "Мастерская" in text
    assert "Сторожка" in text and "Амбар" in text
    assert "щит" in text
    assert f"{B.RAID_VICTIM_SHIELD_HOURS}ч" in text
    assert "перехват" in text
    assert "зарастают" in text or "зарос" in text.lower()
    assert "/вч_я" in text


def test_join_welcome_puts_guide_before_founding():
    from app.domain.guide import game_guide, join_welcome_text

    founding = "🏡 Усадьба основана на А1 (Поле)."
    text = join_welcome_text(founding)
    assert text.startswith("📜")
    assert text.index("Краткий устав") < text.index(founding)
    assert "---" in text
    assert game_guide() in text
    assert founding in text
