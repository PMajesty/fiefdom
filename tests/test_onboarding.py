"""Онбординг: шаги 1→2→3→4, награды, громкий квест в статусе."""
from __future__ import annotations

import os
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.engine import (
    Engine,
    onboard_quest_html,
    try_complete_onboard_build,
    try_complete_onboard_trade,
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
    assert str(B.ONBOARD_DAY2_GOODS) in q2
    assert q3 is not None and q3.startswith("<b>Квест:") and q3.endswith("</b>")
    assert str(B.ONBOARD_DAY3_GRAIN) in q3
    assert onboard_quest_html(1) is None
    assert onboard_quest_html(4) is None


def test_try_complete_onboard_build_advances_with_reward():
    patch = try_complete_onboard_build({"onboard_step": 2, "goods": 10})
    assert patch == {"onboard_step": 3, "goods": 10 + B.ONBOARD_DAY2_GOODS}
    assert try_complete_onboard_build({"onboard_step": 3, "goods": 10}) is None
    assert try_complete_onboard_build({"onboard_step": 1, "goods": 10}) is None


def test_try_complete_onboard_trade_advances_with_reward():
    patch = try_complete_onboard_trade({"onboard_step": 3, "grain": 20})
    assert patch == {"onboard_step": 4, "grain": 20 + B.ONBOARD_DAY3_GRAIN}
    assert try_complete_onboard_trade({"onboard_step": 4, "grain": 20}) is None
    assert try_complete_onboard_trade({"onboard_step": 2, "grain": 20}) is None


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
    db.create_fief.return_value = {"id": 7, "name": "Усадьба Тест"}
    engine = Engine(db)
    engine.maybe_grow_map = MagicMock(return_value=None)  # type: ignore[method-assign]
    user = SimpleNamespace(id=100, full_name="Иван Тестов", first_name="Иван", username="ivan")

    fief, _msg = engine.join_fief(1, user, tile_id=50)

    assert fief["id"] == 7
    assert db.create_fief.call_args.kwargs["onboard_step"] == 2
    assert db.create_fief.call_args.args[0] == 1
    assert db.create_fief.call_args.args[1] == 100


def test_onboard_build_engine_advances_step_and_goods():
    db = _FakeDB({1: {"id": 1, "onboard_step": 2, "goods": 5, "grain": 30}})
    engine = Engine(db)
    engine._onboard_build(1)
    assert db.fiefs[1]["onboard_step"] == 3
    assert db.fiefs[1]["goods"] == 5 + B.ONBOARD_DAY2_GOODS
    # повторно — без двойной награды
    engine._onboard_build(1)
    assert db.fiefs[1]["onboard_step"] == 3
    assert db.fiefs[1]["goods"] == 5 + B.ONBOARD_DAY2_GOODS


def test_onboard_trade_engine_advances_step_and_grain():
    db = _FakeDB({1: {"id": 1, "onboard_step": 3, "goods": 5, "grain": 12}})
    engine = Engine(db)
    engine._onboard_trade(1)
    assert db.fiefs[1]["onboard_step"] == 4
    assert db.fiefs[1]["grain"] == 12 + B.ONBOARD_DAY3_GRAIN
    engine._onboard_trade(1)
    assert db.fiefs[1]["onboard_step"] == 4
    assert db.fiefs[1]["grain"] == 12 + B.ONBOARD_DAY3_GRAIN


def test_post_trade_advances_day3_once():
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
    assert state[1]["onboard_step"] == 4
    # эскроу −10, затем награда дня 3
    assert state[1]["grain"] == 40 - 10 + B.ONBOARD_DAY3_GRAIN

    # повторный post не должен снова начислять день 3
    state[1]["grain"] = 50
    state[1]["goods"] = 30
    engine.post_trade(1, B.RES_GRAIN, 5, B.RES_GOODS, 3)
    assert state[1]["onboard_step"] == 4
    assert state[1]["grain"] == 45  # только эскроу, без повторной награды


def test_accept_trade_advances_day3_without_double_after_post():
    """Продавец уже на шаге 4 (после post_trade) — награда только покупателю."""
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    state = {
        1: {  # buyer, step 3
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
        2: {  # seller, уже завершил день 3 через post_trade
            "id": 2,
            "realm_id": 9,
            "grain": 30,
            "goods": 10,
            "onboard_step": 4,
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

    assert state[1]["onboard_step"] == 4
    assert state[1]["grain"] >= 50 - 10 + B.ONBOARD_DAY3_GRAIN  # оплата + награда (и получение товаров)
    assert state[2]["onboard_step"] == 4
    # продавец не получил повторную ONBOARD_DAY3_GRAIN сверх расчёта сделки
    # grain продавца = 30 + want_amt(10) = 40, без +ONBOARD_DAY3
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
    db.fief_tiles.return_value = [{"is_overgrown": False}]

    text = engine.status_card(1)
    lines = text.split("\n")
    assert lines[0].startswith("🏡 <b>Усадьба А</b>")
    assert lines[1] == onboard_quest_html(2)
    assert "<b>Квест:" in lines[1]
    # квест не в хвосте карточки
    assert not lines[-1].startswith("<b>Квест:")


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
    assert f"Набег и пакт — с дня {B.RAID_PACT_UNLOCK_DAY}." in text
    assert onboard_quest_html(4) is None


def test_guide_mentions_raid_pact_unlock_day():
    from app import balance as B
    from app.domain.guide import game_guide

    text = game_guide()
    assert f"с дня {B.RAID_PACT_UNLOCK_DAY}" in text
    assert "после квестов" in text


def test_guide_explains_patrol():
    from app import balance as B
    from app.domain.guide import game_guide

    text = game_guide()
    assert "<b>Дозор.</b>" in text
    assert f"−{B.PATROL_COST_MIGHT} силы" in text
    assert f"+{B.PATROL_DEFENSE_BONUS} к защите" in text
    assert f"на {B.PATROL_HOURS}ч" in text
    assert "сторожка даёт защиту постоянно" in text
    assert "В тумане дозор почти бесполезен" in text
