"""Караваны: declare (эскроу) → cancel / ночной resolve."""
from __future__ import annotations

import os
from contextlib import nullcontext
from unittest.mock import MagicMock

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.caravans import caravan_is_public
from app.engine import Engine


def _caravan_stateful_engine(
    *,
    grain_from=50,
    goods_from=40,
    grain_to=5,
    goods_to=5,
    barn=0,
    receiver_frozen=False,
    sender_frozen=False,
):
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    sender = {
        "id": 1,
        "realm_id": 10,
        "grain": grain_from,
        "goods": goods_from,
        "frozen": sender_frozen,
        "name": "Альфа",
        "user_id": 100,
    }
    receiver = {
        "id": 2,
        "realm_id": 10,
        "grain": grain_to,
        "goods": goods_to,
        "frozen": receiver_frozen,
        "name": "Бета",
        "user_id": 200,
    }
    fiefs = {1: sender, 2: receiver}
    intents: list[dict] = []

    def get_fief(fid):
        row = fiefs.get(int(fid))
        return dict(row) if row is not None else None

    def debit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    def credit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            row[col] = int(row.get(col) or 0) + int(amt)
        return dict(row)

    def create_action_intent(**fields):
        row = {
            "id": len(intents) + 1,
            "world_id": fields["world_id"],
            "tick_index": fields["tick_index"],
            "fief_id": fields["fief_id"],
            "kind": fields["kind"],
            "payload": dict(fields.get("payload") or {}),
            "status": fields.get("status", "open"),
        }
        intents.append(row)
        return dict(row)

    def list_caravan_intents(wid, tick, statuses=("open", "locked")):
        return [
            dict(i)
            for i in intents
            if int(i["world_id"]) == int(wid)
            and int(i["tick_index"]) == int(tick)
            and i["kind"] == "caravan"
            and i["status"] in statuses
        ]

    def claim_resolve_action_intent(iid):
        for i in intents:
            if int(i["id"]) == int(iid) and i["status"] in ("open", "locked"):
                i["status"] = "resolved"
                return dict(i)
        return None

    def cancel_action_intent(iid, *, statuses=("open",)):
        allowed = set(statuses)
        for i in intents:
            if int(i["id"]) == int(iid) and i["status"] in allowed:
                i["status"] = "cancelled"
                return dict(i)
        return None

    def get_action_intent(iid):
        for i in intents:
            if int(i["id"]) == int(iid):
                return dict(i)
        return None

    def update_open_action_intent_payload(iid, payload):
        for i in intents:
            if int(i["id"]) == int(iid) and i["status"] in ("open", "locked"):
                i["payload"] = dict(payload)
                return dict(i)
        return None

    def update_action_intent_payload(iid, payload):
        for i in intents:
            if int(i["id"]) == int(iid):
                i["payload"] = dict(payload)
                return None
        return None

    def mark_caravan_lock_announced(intent_ids, *, public_ids=()):
        public_set = {int(x) for x in public_ids}
        n = 0
        for iid in intent_ids:
            for i in intents:
                if int(i["id"]) != int(iid) or i.get("kind") != "caravan":
                    continue
                payload = dict(i.get("payload") or {})
                payload["lock_notified"] = True
                if int(iid) in public_set:
                    payload["is_public"] = True
                i["payload"] = payload
                n += 1
        return n

    def mark_caravan_route_public(intent_ids):
        n = 0
        for iid in intent_ids:
            for i in intents:
                if int(i["id"]) != int(iid) or i.get("kind") != "caravan":
                    continue
                payload = dict(i.get("payload") or {})
                payload["is_public"] = True
                i["payload"] = payload
                n += 1
        return n

    def lock_action_intents(wid, tick, *, kind="raid"):
        n = 0
        for i in intents:
            if (
                int(i["world_id"]) == int(wid)
                and int(i["tick_index"]) == int(tick)
                and i["kind"] == kind
                and i["status"] == "open"
            ):
                i["status"] = "locked"
                n += 1
        return n

    db.get_fief.side_effect = get_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.credit_fief_resources.side_effect = credit_fief_resources
    db.create_action_intent.side_effect = create_action_intent
    db.list_caravan_intents.side_effect = list_caravan_intents
    db.claim_resolve_action_intent.side_effect = claim_resolve_action_intent
    db.cancel_action_intent.side_effect = cancel_action_intent
    db.get_action_intent.side_effect = get_action_intent
    db.update_open_action_intent_payload.side_effect = (
        update_open_action_intent_payload
    )
    db.update_action_intent_payload.side_effect = update_action_intent_payload
    db.mark_caravan_lock_announced.side_effect = mark_caravan_lock_announced
    db.mark_caravan_route_public.side_effect = mark_caravan_route_public
    db.lock_action_intents.side_effect = lock_action_intents
    db.get_world.return_value = {
        "id": 1,
        "tick_index": 5,
        "tick_phase": "play",
        "timezone": "UTC",
    }

    db.realms_are_adjacent.return_value = True
    db.get_realm.return_value = {
        "id": 10,
        "world_id": 1,
        "tick_index": 5,
        "active_minor_key": None,
    }
    db.get_active_events.return_value = []
    db.list_active_tile_entities.return_value = []

    engine = Engine(db)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=barn)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine._require_cross_valley_caught_up = MagicMock()
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._format_raid_deadline = MagicMock(
        side_effect=lambda _w, midpoint: "17.07 12:00" if midpoint else "17.07 18:00"
    )
    engine._fiefs = fiefs
    engine._intents = intents
    return engine, fiefs, intents


def test_declare_caravan_success_escrow_and_intent_payload():
    engine, fiefs, intents = _caravan_stateful_engine()
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 12)
    assert fiefs[1]["grain"] == 38
    assert fiefs[2]["grain"] == 5
    assert result.intent_id == 1
    assert result.is_public is False
    assert "Бета" in result.dm_text
    assert "только вы" in result.dm_text
    assert len(intents) == 1
    payload = intents[0]["payload"]
    assert payload == {
        "receiver_id": 2,
        "res": B.RES_GRAIN,
        "amt": 12,
        "escrowed": True,
        "sender_realm_id": 10,
        "receiver_realm_id": 10,
        "is_public": False,
        "lock_notified": False,
    }
    engine.db.create_action_intent.assert_called_once()
    kwargs = engine.db.create_action_intent.call_args.kwargs
    assert kwargs["kind"] == "caravan"
    assert kwargs["status"] == "open"
    assert kwargs["tick_index"] == 5
    assert kwargs["world_id"] == 1


def test_cancel_caravan_intent_refunds():
    engine, fiefs, intents = _caravan_stateful_engine()
    result = engine.declare_caravan(1, 2, B.RES_GOODS, 8)
    assert fiefs[1]["goods"] == 32
    msg = engine.cancel_caravan_intent(1, result.intent_id)
    assert fiefs[1]["goods"] == 40
    assert "возвращён" in msg.lower() or "верну" in msg.lower()
    assert intents[0]["status"] == "cancelled"
    engine.db.credit_fief_resources.assert_called()


def test_resolve_caravan_lands_when_space():
    engine, fiefs, _intents = _caravan_stateful_engine(grain_to=5)
    engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    assert fiefs[1]["grain"] == 40
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 1
    assert fiefs[2]["grain"] == 15
    assert fiefs[1]["grain"] == 40
    assert any("дошёл" in n.text.lower() for n in report.notices if n.kind == "dm")


def test_resolve_caravan_bounces_when_full():
    engine, fiefs, _intents = _caravan_stateful_engine(
        grain_to=B.stash_cap(0)
    )
    engine.declare_caravan(1, 2, B.RES_GRAIN, 5)
    assert fiefs[1]["grain"] == 45
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 1
    assert fiefs[2]["grain"] == B.stash_cap(0)
    assert fiefs[1]["grain"] == 50
    assert any("вернул" in n.text.lower() for n in report.notices if n.kind == "dm")


def test_resolve_caravan_bounces_when_frozen():
    engine, fiefs, _intents = _caravan_stateful_engine()
    engine.declare_caravan(1, 2, B.RES_GRAIN, 7)
    fiefs[2]["frozen"] = True
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 1
    assert fiefs[1]["grain"] == 50
    assert fiefs[2]["grain"] == 5
    assert any("вернул" in n.text.lower() for n in report.notices if n.kind == "dm")


def test_resolve_caravan_idempotent_on_claim_miss():
    engine, fiefs, intents = _caravan_stateful_engine()
    engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    engine.db.claim_resolve_action_intent = MagicMock(return_value=None)
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 0
    assert fiefs[1]["grain"] == 40
    assert fiefs[2]["grain"] == 5
    assert intents[0]["status"] == "open"
    engine.db.credit_fief_resources.assert_not_called()


def test_caravan_is_public_threshold():
    assert caravan_is_public(B.CARAVAN_PUBLIC_AMOUNT - 1) is False
    assert caravan_is_public(B.CARAVAN_PUBLIC_AMOUNT) is True
    assert caravan_is_public(B.CARAVAN_PUBLIC_AMOUNT + 5) is True


def test_declare_public_caravan_sets_flag_without_announce_text():
    engine, _fiefs, intents = _caravan_stateful_engine(grain_from=100)
    amt = B.CARAVAN_PUBLIC_AMOUNT
    result = engine.declare_caravan(1, 2, B.RES_GRAIN, amt)
    assert result.is_public is True
    assert intents[0]["payload"]["is_public"] is True
    assert intents[0]["payload"]["lock_notified"] is False


def test_announce_locked_caravans_aggregates_route_and_is_idempotent():
    engine, _fiefs, intents = _caravan_stateful_engine(grain_from=100, goods_from=80)
    engine.declare_caravan(1, 2, B.RES_GRAIN, 20)
    engine.declare_caravan(1, 2, B.RES_GOODS, 15)
    for row in intents:
        row["status"] = "locked"
    report = engine.announce_locked_caravans(1)
    assert report.announced_intent_count == 2
    assert report.intent_ids == (1, 2)
    dm = [n for n in report.notices if n.kind == "dm"]
    continent = [n for n in report.notices if n.kind == "continent"]
    assert len(dm) == 1
    assert dm[0].user_id == 200
    assert "20" in dm[0].text and "15" in dm[0].text
    assert len(continent) == 1
    assert "Альфа" in continent[0].text and "Бета" in continent[0].text
    assert all(i["payload"]["lock_notified"] is False for i in intents)

    engine.commit_locked_caravan_announcements(
        report.intent_ids, public_ids=report.public_ids
    )
    assert all(i["payload"]["lock_notified"] is True for i in intents)
    assert all(i["payload"]["is_public"] is True for i in intents)

    again = engine.announce_locked_caravans(1)
    assert again.announced_intent_count == 0
    assert again.notices == []


def test_announce_locked_caravans_private_when_route_total_below_threshold():
    engine, _fiefs, intents = _caravan_stateful_engine()
    engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    engine.declare_caravan(1, 2, B.RES_GRAIN, 8)
    for row in intents:
        row["status"] = "locked"
    report = engine.announce_locked_caravans(1)
    assert report.announced_intent_count == 2
    assert any(n.kind == "dm" for n in report.notices)
    assert not any(n.kind == "continent" for n in report.notices)
    engine.commit_locked_caravan_announcements(report.intent_ids)
    assert all(i["payload"]["lock_notified"] is True for i in intents)


def test_announce_treats_missing_lock_notified_as_pending():
    engine, _fiefs, intents = _caravan_stateful_engine()
    engine.declare_caravan(1, 2, B.RES_GRAIN, 5)
    intents[0]["status"] = "locked"
    intents[0]["payload"].pop("lock_notified", None)
    report = engine.announce_locked_caravans(1)
    assert report.announced_intent_count == 1


def test_resolve_public_caravan_adds_public_notices():
    engine, _fiefs, _intents = _caravan_stateful_engine(grain_from=100)
    engine.declare_caravan(1, 2, B.RES_GRAIN, B.CARAVAN_PUBLIC_AMOUNT)
    report = engine.resolve_pending_caravans(1, 5)
    public = [n for n in report.notices if n.kind == "public"]
    assert public
    assert report.digest_lines
    assert any("дошёл" in line.lower() for _rid, line in report.digest_lines)


def test_resolve_caravan_applies_fair_bonus_and_wedding_gift():
    engine, fiefs, _intents = _caravan_stateful_engine(grain_from=80, grain_to=5)
    engine.db.get_realm.return_value = {
        "id": 10,
        "world_id": 1,
        "tick_index": 5,
        "active_minor_key": "fair",
    }
    engine.declare_caravan(1, 2, B.RES_GRAIN, 20)
    report = engine.resolve_pending_caravans(1, 5)
    assert report.resolved_count == 1
    # fair: +5% к доставке (20 → 21)
    assert fiefs[2]["grain"] == 5 + 21

    engine2, fiefs2, _i2 = _caravan_stateful_engine(grain_from=80, grain_to=5)
    engine2.db.get_realm.return_value = {
        "id": 10,
        "world_id": 1,
        "tick_index": 5,
        "active_minor_key": "wedding",
    }
    engine2.declare_caravan(1, 2, B.RES_GRAIN, 10)
    engine2.resolve_pending_caravans(1, 5)
    # доставка 10 + свадебный подарок 5 зерна обеим сторонам
    assert fiefs2[2]["grain"] == 5 + 10 + 5
    assert fiefs2[1]["grain"] == 70 + 5
