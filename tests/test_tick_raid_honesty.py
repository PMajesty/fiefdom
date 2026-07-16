"""Issue 7: farm_mult same-day honesty; raid result for victim DM."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.domain.events import minor_effect
from app.domain.raids import RaidActionResult
from app.domain.tick import FiefTickState
from app.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _base_realm(**overrides):
    data = {
        "id": 1,
        "title": "Долина",
        "chat_id": -100,
        "day_number": 3,
        "timezone": "Europe/Moscow",
        "pending_raid_lines": [],
        "active_minor_key": None,
        "active_minor_until": None,
    }
    data.update(overrides)
    return data


def _base_fief(**overrides):
    data = {
        "id": 10,
        "realm_id": 1,
        "user_id": 1001,
        "name": "Хутор",
        "grain": 50,
        "goods": 20,
        "might": 5,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
        "actions": 1,
        "hungry": False,
        "frozen": False,
        "patrol_until": None,
    }
    data.update(overrides)
    return data


def test_tick_applies_harvest_mult_same_day():
    """Новый harvest крутится до производства — farm_mult тика = 1.25."""
    db = MagicMock()
    realm = _base_realm()
    fief = _base_fief()
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_fiefs.return_value = [fief]
    db.fief_tiles.return_value = [
        {
            "x": 0,
            "y": 0,
            "tile_type": "field",
            "owner_fief_id": 10,
            "building": "farm",
            "building_level": 1,
            "is_core": True,
            "is_overgrown": False,
        }
    ]
    db.get_active_events.return_value = []
    db.raids_since.return_value = []

    captured = {}

    def fake_apply(state: FiefTickState):
        captured["farm_mult"] = state.farm_mult
        out = MagicMock()
        out.grain = state.grain
        out.goods = state.goods
        out.might = state.might
        out.pending_grain = state.pending_grain
        out.pending_goods = state.pending_goods
        out.pending_might = state.pending_might
        out.actions = state.actions + 1
        out.hungry = False
        return out

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])

    def update_realm(rid, **fields):
        realm.update(fields)

    db.update_realm.side_effect = update_realm

    with (
        patch("app.engine.roll_minor_event", return_value="harvest"),
        patch("app.engine.apply_fief_tick", side_effect=fake_apply),
    ):
        result = engine.run_realm_tick(1)

    expected = float(minor_effect("harvest")["farm_mult"])
    assert captured["farm_mult"] == expected
    assert realm["active_minor_key"] == "harvest"
    assert "Урожай" in (result["digest"] or "") or "урожа" in (result["digest"] or "").lower()


def test_tick_drought_mitigated_fief_gets_full_mult():
    """При действующей засухе политая усадьба тикает с farm_mult=1.0."""
    until = _utcnow() + timedelta(hours=6)
    db = MagicMock()
    realm = _base_realm(active_minor_key="drought", active_minor_until=until)
    watered = _base_fief(id=10, name="Политая")
    dry = _base_fief(id=11, user_id=1002, name="Сухая")
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_fiefs.return_value = [watered, dry]
    db.fief_tiles.return_value = [
        {
            "x": 0,
            "y": 0,
            "tile_type": "field",
            "owner_fief_id": 10,
            "building": "farm",
            "building_level": 1,
            "is_core": True,
            "is_overgrown": False,
        }
    ]
    db.get_active_events.return_value = [
        {
            "id": 44,
            "event_key": "drought",
            "status": "active",
            "payload": {"mitigated_fief_ids": [10]},
        }
    ]
    db.raids_since.return_value = []

    mults: list[float] = []

    def fake_apply(state: FiefTickState):
        mults.append(state.farm_mult)
        out = MagicMock()
        out.grain = state.grain
        out.goods = state.goods
        out.might = state.might
        out.pending_grain = 0.0
        out.pending_goods = 0.0
        out.pending_might = 0.0
        out.actions = 2
        out.hungry = False
        return out

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])

    with patch("app.engine.apply_fief_tick", side_effect=fake_apply):
        engine.run_realm_tick(1)

    drought_mult = float(minor_effect("drought")["farm_mult"])
    assert mults == [1.0, drought_mult]

def test_tick_continuing_drought_does_not_reroll():
    """Пока until в будущем — новый ролл не сбрасывает засуху и полив."""
    until = _utcnow() + timedelta(hours=8)
    db = MagicMock()
    realm = _base_realm(active_minor_key="drought", active_minor_until=until)
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_fiefs.return_value = []
    db.get_active_events.return_value = [
        {
            "id": 44,
            "event_key": "drought",
            "payload": {"mitigated_fief_ids": [7]},
        }
    ]
    db.raids_since.return_value = []

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])

    with patch("app.engine.roll_minor_event") as roll:
        engine.run_realm_tick(1)
        roll.assert_not_called()

    db.update_event.assert_not_called()
    assert realm["active_minor_key"] == "drought"


def test_raid_action_result_includes_victim_and_dm_texts():
    r = RaidActionResult(
        public_line="A ограбил B (−3 зерна, −1 товаров)",
        success=True,
        victim_fief_id=2,
        victim_user_id=2002,
        victim_name="B",
        attacker_name="A",
        grain_stolen=3,
        goods_stolen=1,
        intercept_applied=False,
    )
    assert r.victim_user_id == 2002
    assert "A" in r.victim_dm_text()
    assert "3" in r.victim_dm_text()
    assert r.interceptor_dm_text() is None

    fail = RaidActionResult(
        public_line="отбит",
        success=False,
        victim_fief_id=2,
        victim_user_id=2002,
        victim_name="B",
        attacker_name="A",
        grain_stolen=0,
        goods_stolen=0,
        intercept_applied=True,
        interceptor_fief_id=3,
        interceptor_user_id=2003,
    )
    assert "отбит" in fail.victim_dm_text().lower() or "перехватил" in fail.victim_dm_text().lower()
    assert fail.interceptor_dm_text() is not None
    assert "B" in fail.interceptor_dm_text()


def test_engine_raid_returns_victim_user_id():
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    atk = {
        "id": 1,
        "realm_id": 1,
        "user_id": 101,
        "name": "Атакующий",
        "grain": 10,
        "goods": 10,
        "might": 20,
        "hungry": False,
        "last_raid_at": None,
        "actions": 2,
        "pending_grain": 0,
        "pending_goods": 0,
        "pending_might": 0,
        "pact_id": None,
        "shield_until": None,
        "patrol_until": None,
    }
    vic = {
        "id": 2,
        "realm_id": 1,
        "user_id": 202,
        "name": "Жертва",
        "grain": 80,
        "goods": 40,
        "might": 5,
        "hungry": False,
        "shield_until": None,
        "patrol_until": None,
        "pact_id": None,
        "pending_grain": 0,
        "pending_goods": 0,
        "pending_might": 0,
        "actions": 1,
    }
    realm = _base_realm(id=1, active_minor_key=None, active_minor_until=None)

    def get_fief(fid):
        return dict(atk) if fid == 1 else dict(vic)

    db.get_fief.side_effect = get_fief
    db.get_realm.return_value = realm
    db.last_raid_attacker_victim.return_value = None
    db.pact_members.return_value = []
    db.fief_tiles.return_value = []

    engine = Engine(db)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine._spend_action = MagicMock()
    prod = MagicMock()
    prod.defense = 1.0
    prod.grain = 5.0
    prod.goods = 2.0
    engine.fief_prod = MagicMock(return_value=prod)

    result = engine.raid(1, 2, might=10)
    assert isinstance(result, RaidActionResult)
    assert result.victim_user_id == 202
    assert result.victim_fief_id == 2
    assert result.public_line
    assert "Жертва" in result.victim_dm_text() or "атак" in result.victim_dm_text().lower() or "На ваш" in result.victim_dm_text()
