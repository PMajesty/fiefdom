"""Issue 7: farm_mult same-day honesty; raid result for victim DM."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app import balance as B
from app.domain.events import minor_effect
from app.domain.raids import RaidActionResult, RaidResult
from app.domain.tick import FiefTickState
from app.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _base_realm(**overrides):
    data = {
        "id": 1,
        "world_id": 1,
        "chain_index": 0,
        "title": "Долина",
        "chat_id": -100,
        "day_number": 3,
        "timezone": "Europe/Moscow",
        "pending_raid_lines": [],
        "active_minor_key": None,
        "active_minor_until": None,
        "pending_minor_key": None,
        "tick_index": 0,
        "last_economy_tick": 0,
        "forced_tick_count": 0,
        "last_tick_local_date": None,
        "last_tick_slot": None,
    }
    data.update(overrides)
    if "last_economy_tick" not in overrides:
        data["last_economy_tick"] = int(data.get("tick_index") or 0)
    return data


def _attach_world(db, realm, fiefs: list[dict], realms: list[dict] | None = None) -> dict:
    """Мок континента для run_realm_tick → run_world_tick."""
    chain = list(realms) if realms is not None else [realm]
    world = {
        "id": 1,
        "name": "Континент",
        "day_number": realm.get("day_number", 1),
        "tick_index": realm.get("tick_index", 0),
        "timezone": realm.get("timezone") or "Europe/Moscow",
        "last_tick_local_date": realm.get("last_tick_local_date"),
        "last_tick_slot": realm.get("last_tick_slot"),
        "forced_tick_count": realm.get("forced_tick_count", 0),
        "active_minor_key": realm.get("active_minor_key"),
        "pending_minor_key": realm.get("pending_minor_key"),
        "active_minor_until": None,
        "next_catastrophe_tick": 99,
        "next_catastrophe_key": None,
        "last_catastrophe_key": None,
    }
    db.transaction.return_value = nullcontext()
    db.get_or_create_world.return_value = world
    db.get_world.return_value = world
    db.list_realms_by_chain.return_value = chain

    def sync(_wid):
        for r in chain:
            for k in (
                "day_number",
                "tick_index",
                "timezone",
                "last_tick_at",
                "last_tick_local_date",
                "last_tick_slot",
                "active_minor_key",
                "pending_minor_key",
                "forced_tick_count",
                "next_catastrophe_tick",
                "next_catastrophe_key",
                "last_catastrophe_key",
            ):
                if k in world:
                    r[k] = world[k]

    db.sync_realms_clock_from_world.side_effect = sync

    def update_world(_wid, **fields):
        world.update(fields)
        sync(_wid)

    def update_realm(rid, **fields):
        for r in chain:
            if int(r["id"]) == int(rid):
                r.update(fields)
                break

    db.update_world.side_effect = update_world
    db.update_realm.side_effect = update_realm
    db.get_user.side_effect = lambda uid: {
        "telegram_id": uid,
        "last_realm_id": 1,
    }
    db.list_fiefs_by_user.side_effect = lambda uid: [
        f for f in fiefs if int(f["user_id"]) == int(uid)
    ]
    return world


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
    """Новый harvest крутится до производства - farm_mult тика из minor_effect."""
    db = MagicMock()
    realm = _base_realm()
    fief = _base_fief()
    _attach_world(db, realm, [fief])
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_expired_open_trades.return_value = []
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
    db.raids_since_tick.return_value = []

    captured = {}

    def fake_apply(state: FiefTickState):
        captured["farm_mult"] = state.farm_mult
        out = MagicMock()
        out.balance_columns.return_value = {
            **state.stash,
            **{f"pending_{k}": v for k, v in state.pending.items()},
        }
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


def test_tick_drought_applies_farm_mult_to_all_fiefs():
    """При засухе этого тика все усадьбы тикают с farm_mult засухи."""
    db = MagicMock()
    realm = _base_realm()
    a = _base_fief(id=10, name="А")
    b = _base_fief(id=11, user_id=1002, name="Б")
    _attach_world(db, realm, [a, b])
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_expired_open_trades.return_value = []
    db.list_fiefs.return_value = [a, b]
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
    db.raids_since_tick.return_value = []

    def update_realm(rid, **fields):
        realm.update(fields)

    db.update_realm.side_effect = update_realm

    mults: list[float] = []

    def fake_apply(state: FiefTickState):
        mults.append(state.farm_mult)
        out = MagicMock()
        out.balance_columns.return_value = {
            **state.stash,
            **{f"pending_{k}": v for k, v in state.pending.items()},
        }
        out.actions = 2
        out.hungry = False
        return out

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])

    with (
        patch("app.engine.roll_minor_event", return_value="drought"),
        patch("app.engine.apply_fief_tick", side_effect=fake_apply),
    ):
        engine.run_realm_tick(1)

    drought_mult = float(minor_effect("drought")["farm_mult"])
    assert mults == [drought_mult, drought_mult]


def test_tick_always_rerolls_minor_even_if_key_active():
    """Каждый тик закрывает старый минор и крутит новый."""
    db = MagicMock()
    realm = _base_realm(active_minor_key="harvest", tick_index=3)
    _attach_world(db, realm, [])
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_expired_open_trades.return_value = []
    db.list_fiefs.return_value = []
    db.get_active_events.return_value = [
        {
            "id": 44,
            "event_key": "harvest",
            "status": "active",
            "payload": {},
        }
    ]
    db.raids_since_tick.return_value = []

    def update_realm(rid, **fields):
        realm.update(fields)

    db.update_realm.side_effect = update_realm

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])

    with patch("app.engine.roll_minor_event", return_value="fog") as roll:
        engine.run_realm_tick(1)
        # Тик: ролл текущего минора (если pending пуст) + преролл следующего для слухов.
        assert roll.call_count == 2

    db.update_event.assert_called_once_with(44, status="resolved")
    assert realm["active_minor_key"] == "fog"
    assert realm.get("pending_minor_key") == "fog"


def test_raid_action_result_includes_victim_and_dm_texts():
    r = RaidActionResult(
        public_line="A ограбил B",
        success=True,
        victim_fief_id=2,
        victim_user_id=2002,
        victim_name="B",
        attacker_name="A",
        stolen={B.RES_GRAIN: 3, B.RES_GOODS: 1},
        intercept_applied=False,
    )
    assert r.victim_user_id == 2002
    assert "A" in r.victim_dm_text()
    assert "3" in r.victim_dm_text()
    assert "3" in r.attacker_dm_text()
    assert "1" in r.attacker_dm_text()
    assert "зерна" not in r.public_line
    assert "товаров" not in r.public_line
    assert r.interceptor_dm_text() is None

    fail = RaidActionResult(
        public_line="отбит",
        success=False,
        victim_fief_id=2,
        victim_user_id=2002,
        victim_name="B",
        attacker_name="A",
        stolen={B.RES_GRAIN: 0, B.RES_GOODS: 0},
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
        "last_raid_tick": None,
        "actions": 2,
        "pending_grain": 0,
        "pending_goods": 0,
        "pending_might": 0,
        "pact_id": None,
        "shield_until": None,
        "shield_until_tick": None,
        "patrol_until": None,
        "patrol_until_tick": None,
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
        "shield_until_tick": None,
        "patrol_until": None,
        "patrol_until_tick": None,
        "pact_id": None,
        "pending_grain": 0,
        "pending_goods": 0,
        "pending_might": 0,
        "actions": 1,
    }
    realm = _base_realm(id=1, active_minor_key=None, active_minor_until=None, tick_index=4)

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
    prod.resources.return_value = {B.RES_GRAIN: 5.0, B.RES_GOODS: 2.0, B.RES_MIGHT: 0.0}
    engine.fief_prod = MagicMock(return_value=prod)

    result = engine.raid(1, 2, might=10)
    assert isinstance(result, RaidActionResult)
    assert result.victim_user_id == 202
    assert result.victim_fief_id == 2
    assert result.public_line
    assert "Жертва" in result.victim_dm_text() or "атак" in result.victim_dm_text().lower() or "На ваш" in result.victim_dm_text()


def _tick_engine_with_schedule(realm: dict):
    db = MagicMock()
    fief = _base_fief()
    _attach_world(db, realm, [fief])
    db.get_realm.return_value = realm
    db.list_open_trades.return_value = []
    db.list_expired_open_trades.return_value = []
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
    db.raids_since_tick.return_value = []

    def update_realm(rid, **fields):
        realm.update(fields)

    db.update_realm.side_effect = update_realm

    engine = Engine(db)
    engine.apply_absence = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.maybe_grow_map = MagicMock(return_value=None)
    engine._feud_lines = MagicMock(return_value=[])
    return engine, db


def test_manual_tick_does_not_advance_schedule_markers():
    realm = _base_realm(
        last_tick_local_date=None,
        last_tick_slot=None,
        day_number=3,
    )
    engine, _db = _tick_engine_with_schedule(realm)

    def fake_apply(state: FiefTickState):
        out = MagicMock()
        out.balance_columns.return_value = {
            **state.stash,
            **{f"pending_{k}": v for k, v in state.pending.items()},
        }
        out.actions = state.actions + 1
        out.hungry = False
        return out

    with (
        patch("app.engine.roll_minor_event", return_value=None),
        patch("app.engine.apply_fief_tick", side_effect=fake_apply),
    ):
        engine.run_realm_tick(1)

    assert realm["day_number"] == 3
    assert "last_tick_at" in realm
    assert realm.get("last_tick_local_date") is None
    assert realm.get("last_tick_slot") is None


def test_scheduled_tick_writes_slot_markers():
    realm = _base_realm(
        last_tick_local_date=None,
        last_tick_slot=None,
        day_number=3,
    )
    engine, _db = _tick_engine_with_schedule(realm)

    def fake_apply(state: FiefTickState):
        out = MagicMock()
        out.balance_columns.return_value = {
            **state.stash,
            **{f"pending_{k}": v for k, v in state.pending.items()},
        }
        out.actions = state.actions + 1
        out.hungry = False
        return out

    with (
        patch("app.engine.roll_minor_event", return_value=None),
        patch("app.engine.apply_fief_tick", side_effect=fake_apply),
    ):
        engine.run_realm_tick(1, tick_slot=0)

    # Первая установка курсора даты не бампит день (нет prev_local для сравнения).
    assert realm["day_number"] == 3
    assert realm.get("last_tick_slot") == 0
    assert realm.get("last_tick_local_date") is not None


def _raid_stateful_engine(*, atk_extra=None, vic_extra=None, reverse_pair_at=None):
    from app import balance as B

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
        "last_raid_tick": None,
        "actions": 2,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
        "pact_id": None,
        "shield_until": None,
        "shield_until_tick": None,
        "patrol_until": None,
        "patrol_until_tick": None,
        "last_active_at": _utcnow(),
        "last_active_tick": 0,
    }
    if atk_extra:
        atk.update(atk_extra)
    vic = {
        "id": 2,
        "realm_id": 1,
        "user_id": 202,
        "name": "Жертва",
        "grain": 40,
        "goods": 20,
        "might": 10,
        "hungry": False,
        "shield_until": None,
        "shield_until_tick": None,
        "patrol_until": None,
        "patrol_until_tick": None,
        "pact_id": None,
        "pending_grain": 30.0,
        "pending_goods": 12.0,
        "pending_might": 8.0,
        "actions": 1,
        "last_active_at": _utcnow(),
        "last_active_tick": 0,
    }
    if vic_extra:
        vic.update(vic_extra)
    fiefs = {1: atk, 2: vic}
    realm = _base_realm(id=1, active_minor_key=None, active_minor_until=None, tick_index=10)
    pair_log: dict[tuple[int, int], int] = {}
    if reverse_pair_at is not None:
        pair_log[(2, 1)] = reverse_pair_at

    db = MagicMock()
    db.transaction = lambda: nullcontext()

    def get_fief(fid):
        row = fiefs.get(fid)
        return dict(row) if row else None

    def update_fief(fid, **fields):
        fiefs[fid].update(fields)

    def debit_fief_resources(fid, **amounts):
        row = fiefs[int(fid)]
        for col, amt in amounts.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    def last_pair(a, v):
        return pair_log.get((a, v))

    def log_raid(**kwargs):
        pair_log[(kwargs["attacker_fief_id"], kwargs["victim_fief_id"])] = int(
            kwargs.get("tick_index") or 10
        )

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.get_realm.return_value = realm
    db.update_realm = MagicMock()
    db.last_raid_attacker_victim.side_effect = last_pair
    db.log_raid.side_effect = log_raid
    db.pact_members.return_value = []
    db.fief_tiles.return_value = []

    engine = Engine(db)
    engine.barn_level = MagicMock(return_value=0)
    engine._spend_action = MagicMock()
    prod = MagicMock()
    prod.defense = 1.0
    prod.resources.return_value = {B.RES_GRAIN: 5.0, B.RES_GOODS: 2.0, B.RES_MIGHT: 0.0}
    engine.fief_prod = MagicMock(return_value=prod)
    return engine, fiefs, B


def test_raid_does_not_bank_victim_pending_might():
    engine, fiefs, _B = _raid_stateful_engine()
    result = engine.raid(1, 2, might=10)
    assert fiefs[2]["might"] == 10
    assert fiefs[2]["pending_might"] == 8.0
    assert fiefs[2]["pending_grain"] == 0.0
    assert fiefs[2]["pending_goods"] == 0.0
    # pending зерно/товары вошли в stash, затем могла уйти добыча
    assert fiefs[2]["grain"] == 70 - result.stolen[B.RES_GRAIN]
    assert fiefs[2]["goods"] == 32 - result.stolen[B.RES_GOODS]


def test_engine_raid_passes_victim_might_into_defense():
    engine, fiefs, _B = _raid_stateful_engine(
        vic_extra={
            "might": 27,
            "pending_grain": 0.0,
            "pending_goods": 0.0,
            "pending_might": 0.0,
        },
    )
    with patch("app.engine.resolve_raid") as resolve:
        resolve.return_value = RaidResult(
            success=False,
            ratio=0.2,
            might_lost=10,
            stolen={B.RES_GRAIN: 0, B.RES_GOODS: 0},
            defense_used=28,
            intercept_applied=False,
            public_line="отбит",
        )
        engine.raid(1, 2, might=10)
    assert resolve.call_args.kwargs["victim_might"] == 27
    assert fiefs[2]["might"] == 27


def test_raid_bidirectional_pair_cooldown_blocks_revenge():
    engine, fiefs, B = _raid_stateful_engine(
        reverse_pair_at=10,
        vic_extra={"pending_grain": 0.0, "pending_goods": 0.0, "pending_might": 0.0},
    )
    try:
        engine.raid(1, 2, might=10)
        raise AssertionError("expected pair cooldown")
    except ValueError as e:
        assert "пару" in str(e).lower() or "кулдаун" in str(e).lower()
    assert fiefs[1]["might"] == 20


def test_raid_has_no_personal_attacker_cooldown():
    """Личный кулдаун нападающего снят: недавний last_raid_tick сам по себе не блокирует."""
    engine, fiefs, B = _raid_stateful_engine(
        atk_extra={"last_raid_tick": 10, "might": 30},
        vic_extra={"pending_grain": 0.0, "pending_goods": 0.0, "pending_might": 0.0},
    )
    with patch("app.engine.resolve_raid") as resolve:
        resolve.return_value = RaidResult(
            success=True,
            ratio=1.0,
            might_lost=5,
            stolen={B.RES_GRAIN: 1, B.RES_GOODS: 0},
            defense_used=1,
            intercept_applied=False,
            public_line="ok",
        )
        result = engine.raid(1, 2, might=10)
    assert result.success is True
    assert fiefs[1]["might"] == 25
    assert not hasattr(B, "RAID_ATTACKER_COOLDOWN_TICKS")


def test_raid_shield_blocks_outgoing():
    engine, fiefs, B = _raid_stateful_engine(
        atk_extra={"shield_until_tick": 20},
        vic_extra={"pending_grain": 0.0, "pending_goods": 0.0, "pending_might": 0.0},
    )
    try:
        engine.raid(1, 2, might=10)
        raise AssertionError("expected shield block")
    except ValueError as e:
        assert "щит" in str(e).lower()
    assert fiefs[1]["might"] == 20


def test_successful_raid_grants_one_tick_global_shield():
    engine, fiefs, B = _raid_stateful_engine(
        vic_extra={"pending_grain": 0.0, "pending_goods": 0.0, "pending_might": 0.0},
    )
    with patch("app.engine.resolve_raid") as resolve:
        resolve.return_value = RaidResult(
            success=True,
            ratio=1.0,
            might_lost=5,
            stolen={B.RES_GRAIN: 1, B.RES_GOODS: 0},
            defense_used=1,
            intercept_applied=False,
            public_line="ok",
        )
        engine.raid(1, 2, might=10)
    assert fiefs[2]["shield_until_tick"] == 10 + B.RAID_VICTIM_SHIELD_TICKS
    assert B.RAID_VICTIM_SHIELD_TICKS == 1


def test_victim_shield_blocks_any_attacker():
    """Щит общий: второй нападающий тоже не проходит, не только автор удара."""
    engine, fiefs, B = _raid_stateful_engine(
        vic_extra={
            "shield_until_tick": 11,
            "pending_grain": 0.0,
            "pending_goods": 0.0,
            "pending_might": 0.0,
        },
    )
    try:
        engine.raid(1, 2, might=10)
        raise AssertionError("expected global shield block")
    except ValueError as e:
        assert "щит" in str(e).lower()
    assert fiefs[1]["might"] == 20
