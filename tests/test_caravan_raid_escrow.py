"""Part A: исходящий эскроу обозов лутается ночным набегом (у ворот)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

from app import balance as B
from app.domain.caravans import (
    format_caravan_intercepted_gate_line,
    open_caravan_escrow_bag,
    plan_caravan_escrow_debits,
)
from app.domain.guide import game_guide
from app.domain.raids import (
    raid_loot_pool,
    resolve_raid,
    split_loot_prefer_escrow,
    unprotected_stash,
)
from app.domain.resource_bags import empty_loot_bag
from tests.test_caravans import _caravan_stateful_engine
from tests.test_raid_night_characterization import (
    _base_fief,
    _inject_raid_intent,
    _raid_night_engine,
)


def test_open_caravan_escrow_bag_sums_tradeable_road_only():
    intents = [
        {
            "id": 1,
            "kind": "caravan",
            "status": "open",
            "payload": {"res": B.RES_GRAIN, "amt": 40},
        },
        {
            "id": 2,
            "kind": "caravan",
            "status": "locked",
            "payload": {"res": B.RES_GOODS, "amt": 10},
        },
        {
            "id": 3,
            "kind": "caravan",
            "status": "cancelled",
            "payload": {"res": B.RES_GRAIN, "amt": 99},
        },
        {
            "id": 4,
            "kind": "raid",
            "status": "open",
            "payload": {"res": B.RES_GRAIN, "amt": 5},
        },
    ]
    bag = open_caravan_escrow_bag(intents)
    assert bag[B.RES_GRAIN] == 40
    assert bag[B.RES_GOODS] == 10


def test_raid_loot_pool_adds_full_escrow_barn_only_on_stash():
    stash = {B.RES_GRAIN: 100, B.RES_GOODS: 0}
    barn = 2
    protect = B.barn_protect_frac(barn)
    unprot = unprotected_stash(stash, barn)
    assert unprot[B.RES_GRAIN] == int(100 * (1.0 - protect))
    escrow = {B.RES_GRAIN: 80, B.RES_GOODS: 0}
    pool = raid_loot_pool(stash, barn, escrow=escrow)
    assert pool[B.RES_GRAIN] == unprot[B.RES_GRAIN] + 80


def test_split_loot_prefer_escrow_drains_gate_first():
    stolen = {B.RES_GRAIN: 30, B.RES_GOODS: 5}
    escrow = {B.RES_GRAIN: 20, B.RES_GOODS: 0}
    from_escrow, from_stash = split_loot_prefer_escrow(stolen, escrow)
    assert from_escrow[B.RES_GRAIN] == 20
    assert from_stash[B.RES_GRAIN] == 10
    assert from_escrow[B.RES_GOODS] == 0
    assert from_stash[B.RES_GOODS] == 5


def test_plan_caravan_escrow_debits_partial_then_cancel():
    intents = [
        {
            "id": 2,
            "kind": "caravan",
            "status": "open",
            "payload": {"res": B.RES_GRAIN, "amt": 10},
        },
        {
            "id": 1,
            "kind": "caravan",
            "status": "open",
            "payload": {"res": B.RES_GRAIN, "amt": 15},
        },
    ]
    plan = plan_caravan_escrow_debits(intents, {B.RES_GRAIN: 20})
    assert [(p.intent_id, p.taken, p.remaining_amt) for p in plan] == [
        (1, 15, 0),
        (2, 5, 5),
    ]


def test_resolve_raid_sizes_against_escrow_dump():
    """Пустой двор + жирный обоз всё ещё даёт добычу (feeder dump)."""
    stash = empty_loot_bag()
    escrow = {B.RES_GRAIN: 200, B.RES_GOODS: 0}
    import random

    rng = random.Random(7)
    result = resolve_raid(
        attacker_name="A",
        victim_name="V",
        attack_might=80,
        watch_defense=1.0,
        patrol_active=False,
        intercept=False,
        victim_stash=stash,
        barn_level=3,
        victim_daily={B.RES_GRAIN: 10.0, B.RES_GOODS: 2.0},
        victim_might=0,
        escrow_stash=escrow,
        rng=rng,
    )
    assert result.success
    assert result.stolen[B.RES_GRAIN] > 0
    empty = resolve_raid(
        attacker_name="A",
        victim_name="V",
        attack_might=80,
        watch_defense=1.0,
        patrol_active=False,
        intercept=False,
        victim_stash=stash,
        barn_level=3,
        victim_daily={B.RES_GRAIN: 10.0, B.RES_GOODS: 2.0},
        victim_might=0,
        escrow_stash=None,
        rng=random.Random(7),
    )
    assert empty.success
    assert empty.stolen[B.RES_GRAIN] == 0


def test_night_raid_loots_outbound_escrow_prefers_gate():
    atk = _base_fief(
        1, realm_id=1, user_id=101, name="Атакующий", might=5, grain=0, goods=0
    )
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=0,
        grain=0,
        goods=0,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        watch_defense=1.0,
        tick_index=10,
    )
    engine.barn_level = MagicMock(return_value=0)
    engine.db.create_action_intent(
        world_id=1,
        tick_index=10,
        fief_id=2,
        kind="caravan",
        status="open",
        payload={
            "receiver_id": 99,
            "res": B.RES_GRAIN,
            "amt": 100,
            "escrowed": True,
            "sender_realm_id": 1,
            "receiver_realm_id": 1,
            "is_public": False,
        },
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=60, tick_index=10
    )
    fixed = MagicMock()
    fixed.success = True
    fixed.ratio = 2.0
    fixed.might_lost = 0
    fixed.stolen = {B.RES_GRAIN: 40, B.RES_GOODS: 0}
    fixed.defense_used = 1
    fixed.intercept_applied = False
    fixed.public_line = "Атакующий ограбил Жертва"
    with patch("app.services.night_raids.resolve_raid", return_value=fixed):
        report = engine.resolve_pending_raids(1, 10)
    assert report.resolved_count == 1
    assert engine._fiefs[1]["grain"] == 40
    assert engine._fiefs[2]["grain"] == 0
    caravan = engine._intents[0]
    assert caravan["kind"] == "caravan"
    assert caravan["status"] == "open"
    assert caravan["payload"]["amt"] == 60
    gate = format_caravan_intercepted_gate_line()
    dm = [n.text for n in report.notices if n.kind == "dm" and n.user_id == 202]
    assert any(gate in t for t in dm)
    public = [n.text for n in report.notices if n.kind == "public"]
    assert any(gate in t for t in public)
    assert gate in engine._realms[1]["pending_raid_lines"]


def test_night_raid_mixed_stash_and_escrow_prefers_gate():
    """Ночной apply: сначала ворота, потом двор; амбар кроет только двор."""
    atk = _base_fief(
        1, realm_id=1, user_id=101, name="Атакующий", might=5, grain=0, goods=0
    )
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=0,
        grain=100,
        goods=0,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        watch_defense=1.0,
        tick_index=10,
    )
    engine.barn_level = MagicMock(return_value=2)
    engine.db.create_action_intent(
        world_id=1,
        tick_index=10,
        fief_id=2,
        kind="caravan",
        status="locked",
        payload={
            "receiver_id": 99,
            "res": B.RES_GRAIN,
            "amt": 40,
            "escrowed": True,
            "sender_realm_id": 1,
            "receiver_realm_id": 1,
            "is_public": False,
        },
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=60, tick_index=10
    )
    fixed = MagicMock()
    fixed.success = True
    fixed.ratio = 2.0
    fixed.might_lost = 0
    fixed.stolen = {B.RES_GRAIN: 50, B.RES_GOODS: 0}
    fixed.defense_used = 1
    fixed.intercept_applied = False
    fixed.public_line = "Атакующий ограбил Жертва"
    with patch("app.services.night_raids.resolve_raid", return_value=fixed):
        report = engine.resolve_pending_raids(1, 10)
    assert report.resolved_count == 1
    assert engine._fiefs[1]["grain"] == 50
    # 40 с обоза + 10 со двора; амбарный хвост (40%) остаётся в 90.
    assert engine._fiefs[2]["grain"] == 90
    caravan = engine._intents[0]
    assert caravan["kind"] == "caravan"
    assert caravan["status"] == "cancelled"
    assert caravan["payload"]["amt"] == 0
    gate = format_caravan_intercepted_gate_line()
    dm = [n.text for n in report.notices if n.kind == "dm" and n.user_id == 202]
    assert any(gate in t for t in dm)


def test_night_raid_full_escrow_drain_cancels_caravan():
    """Полное списание обоза: cancel_action_intent, без возврата на двор жертвы."""
    atk = _base_fief(
        1, realm_id=1, user_id=101, name="Атакующий", might=5, grain=0, goods=0
    )
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=0,
        grain=0,
        goods=0,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        watch_defense=1.0,
        tick_index=10,
    )
    engine.barn_level = MagicMock(return_value=0)
    engine.db.create_action_intent(
        world_id=1,
        tick_index=10,
        fief_id=2,
        kind="caravan",
        status="open",
        payload={
            "receiver_id": 99,
            "res": B.RES_GRAIN,
            "amt": 50,
            "escrowed": True,
            "sender_realm_id": 1,
            "receiver_realm_id": 1,
            "is_public": False,
        },
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=60, tick_index=10
    )
    fixed = MagicMock()
    fixed.success = True
    fixed.ratio = 2.0
    fixed.might_lost = 0
    fixed.stolen = {B.RES_GRAIN: 50, B.RES_GOODS: 0}
    fixed.defense_used = 1
    fixed.intercept_applied = False
    fixed.public_line = "Атакующий ограбил Жертва"
    with patch("app.services.night_raids.resolve_raid", return_value=fixed):
        report = engine.resolve_pending_raids(1, 10)
    assert report.resolved_count == 1
    assert engine._fiefs[1]["grain"] == 50
    assert engine._fiefs[2]["grain"] == 0
    caravan = engine._intents[0]
    assert caravan["kind"] == "caravan"
    assert caravan["status"] == "cancelled"
    assert caravan["payload"]["amt"] == 0
    gate = format_caravan_intercepted_gate_line()
    dm = [n.text for n in report.notices if n.kind == "dm" and n.user_id == 202]
    assert any(gate in t for t in dm)


def test_night_raid_barn_still_protects_real_stash_not_escrow():
    """Амбар режет двор; эскроу входит в пул целиком."""
    stash = {B.RES_GRAIN: 100, B.RES_GOODS: 0}
    barn = 2
    escrow = {B.RES_GRAIN: 50, B.RES_GOODS: 0}
    pool = raid_loot_pool(stash, barn, escrow=escrow)
    yard_only = unprotected_stash(stash, barn)
    assert pool[B.RES_GRAIN] == yard_only[B.RES_GRAIN] + 50
    assert yard_only[B.RES_GRAIN] < 100


def test_cancel_after_partial_escrow_loot_refunds_remaining_only():
    engine, fiefs, intents = _caravan_stateful_engine(grain_from=50)
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 30)
    assert fiefs[1]["grain"] == 20
    intents[0]["payload"]["amt"] = 12
    msg = engine.cancel_caravan_intent(1, result.intent_id)
    assert fiefs[1]["grain"] == 32
    assert "12" in msg
    assert intents[0]["status"] == "cancelled"


def test_cancel_fully_looted_caravan_fails():
    engine, fiefs, intents = _caravan_stateful_engine(grain_from=50)
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 20)
    intents[0]["status"] = "cancelled"
    intents[0]["payload"]["amt"] = 0
    try:
        engine.cancel_caravan_intent(1, result.intent_id)
        assert False, "expected cancel to fail"
    except ValueError as exc:
        assert "не вернуть" in str(exc).lower() or "не найден" in str(exc).lower()
    assert fiefs[1]["grain"] == 30


def test_night_raid_cancel_race_does_not_mint_from_stale_escrow():
    """Отмена после list, до CAS: атакующий не получает груз, экономика сохраняется."""
    atk = _base_fief(
        1, realm_id=1, user_id=101, name="Атакующий", might=5, grain=0, goods=0
    )
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=0,
        grain=0,
        goods=0,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        watch_defense=1.0,
        tick_index=10,
    )
    engine.barn_level = MagicMock(return_value=0)
    engine.db.create_action_intent(
        world_id=1,
        tick_index=10,
        fief_id=2,
        kind="caravan",
        status="open",
        payload={
            "receiver_id": 99,
            "res": B.RES_GRAIN,
            "amt": 100,
            "escrowed": True,
            "sender_realm_id": 1,
            "receiver_realm_id": 1,
            "is_public": False,
        },
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=60, tick_index=10
    )

    def list_then_player_cancels(fid):
        rows = [
            dict(i)
            for i in engine._intents
            if int(i["fief_id"]) == int(fid)
            and i["kind"] == "caravan"
            and i["status"] == "open"
        ]
        for i in engine._intents:
            if i["kind"] == "caravan" and i["status"] == "open":
                refund = int(i["payload"].get("amt") or 0)
                engine._fiefs[2]["grain"] = (
                    int(engine._fiefs[2].get("grain") or 0) + refund
                )
                i["status"] = "cancelled"
        return rows

    engine.db.list_road_caravan_intents_for_fief.side_effect = (
        list_then_player_cancels
    )
    fixed = MagicMock()
    fixed.success = True
    fixed.ratio = 2.0
    fixed.might_lost = 0
    fixed.stolen = {B.RES_GRAIN: 40, B.RES_GOODS: 0}
    fixed.defense_used = 1
    fixed.intercept_applied = False
    fixed.public_line = "Атакующий ограбил Жертва"
    with patch("app.services.night_raids.resolve_raid", return_value=fixed):
        report = engine.resolve_pending_raids(1, 10)
    assert report.resolved_count == 1
    assert engine._fiefs[1]["grain"] == 0
    assert engine._fiefs[2]["grain"] == 100
    caravan = engine._intents[0]
    assert caravan["status"] == "cancelled"
    assert caravan["payload"]["amt"] == 100


def test_guide_mentions_caravan_gate_intercept():
    text = game_guide()
    assert "перехватить груз у ворот" in text
    assert "амбар тот обоз не кроет" in text
    assert "первой половине окна тика, что и набег" in text
