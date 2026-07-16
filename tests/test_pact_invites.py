"""Пакт: приглашение требует явного принятия."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from app import balance as B
from app.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pact_engine():
    founder = {
        "id": 1,
        "realm_id": 9,
        "user_id": 101,
        "name": "Основатель",
        "pact_id": 50,
        "cover_allies": True,
    }
    target = {
        "id": 2,
        "realm_id": 9,
        "user_id": 202,
        "name": "Гость",
        "pact_id": None,
        "cover_allies": True,
    }
    pact = {
        "id": 50,
        "realm_id": 9,
        "name": "Север",
        "founder_fief_id": 1,
    }
    fiefs = {1: founder, 2: target}
    invites: dict[int, dict] = {}
    next_invite_id = {"n": 1}

    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_realm.return_value = {"id": 9, "tick_index": 5}

    def get_fief(fid):
        row = fiefs.get(fid)
        return dict(row) if row else None

    def update_fief(fid, **fields):
        fiefs[fid].update(fields)

    def get_open(pact_id, target_id):
        for inv in invites.values():
            if (
                inv["pact_id"] == pact_id
                and inv["target_fief_id"] == target_id
                and inv["status"] == "open"
                and int(inv["expires_tick"]) > 5
            ):
                return dict(inv)
        return None

    def create_invite(**fields):
        iid = next_invite_id["n"]
        next_invite_id["n"] += 1
        inv = {
            "id": iid,
            "status": "open",
            **fields,
        }
        invites[iid] = inv
        return dict(inv)

    def get_invite(iid):
        row = invites.get(iid)
        return dict(row) if row else None

    def claim(iid, status):
        inv = invites.get(iid)
        if not inv or inv["status"] != "open" or int(inv["expires_tick"]) <= 5:
            return None
        inv["status"] = status
        return dict(inv)

    def update_invite(iid, **fields):
        invites[iid].update(fields)

    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.get_pact.return_value = pact
    db.pact_members.side_effect = lambda pid: [
        dict(f) for f in fiefs.values() if f.get("pact_id") == pid
    ]
    db.get_open_pact_invite.side_effect = get_open
    db.create_pact_invite.side_effect = create_invite
    db.get_pact_invite.side_effect = get_invite
    db.claim_open_pact_invite.side_effect = claim
    db.update_pact_invite.side_effect = update_invite

    engine = Engine(db)
    return engine, fiefs, invites, pact


def test_invite_to_pact_does_not_join_target():
    engine, fiefs, invites, _pact = _pact_engine()
    invite = engine.invite_to_pact(1, 2)
    assert invite["id"] in invites
    assert invites[invite["id"]]["status"] == "open"
    assert fiefs[2]["pact_id"] is None


def test_accept_pact_invite_joins_with_cover_off():
    engine, fiefs, invites, pact = _pact_engine()
    invite = engine.invite_to_pact(1, 2)
    msg = engine.accept_pact_invite(2, invite["id"])
    assert "Север" in msg
    assert fiefs[2]["pact_id"] == pact["id"]
    assert fiefs[2]["cover_allies"] is False
    assert invites[invite["id"]]["status"] == "accepted"


def test_decline_pact_invite_does_not_join():
    engine, fiefs, invites, _pact = _pact_engine()
    invite = engine.invite_to_pact(1, 2)
    msg = engine.decline_pact_invite(2, invite["id"])
    assert "отклон" in msg.lower()
    assert fiefs[2]["pact_id"] is None
    assert invites[invite["id"]]["status"] == "declined"


def test_accept_rejects_wrong_fief():
    engine, _fiefs, _invites, _pact = _pact_engine()
    invite = engine.invite_to_pact(1, 2)
    try:
        engine.accept_pact_invite(1, invite["id"])
        raise AssertionError("expected wrong target")
    except ValueError as e:
        assert "не вам" in str(e).lower()


def test_accept_rejects_expired_invite():
    engine, fiefs, invites, _pact = _pact_engine()
    invite = engine.invite_to_pact(1, 2)
    invites[invite["id"]]["expires_tick"] = 0
    try:
        engine.accept_pact_invite(2, invite["id"])
        raise AssertionError("expected expired")
    except ValueError as e:
        assert "истекл" in str(e).lower()
    assert fiefs[2]["pact_id"] is None


def test_duplicate_open_invite_rejected():
    engine, _fiefs, _invites, _pact = _pact_engine()
    engine.invite_to_pact(1, 2)
    try:
        engine.invite_to_pact(1, 2)
        raise AssertionError("expected duplicate reject")
    except ValueError as e:
        assert "уже" in str(e).lower()


def test_invite_rejects_when_pact_full():
    engine, fiefs, _invites, pact = _pact_engine()
    # Основатель уже в пакте; добиваем до MAX участников.
    for i in range(3, 2 + B.PACT_SIZE_MAX):
        fiefs[i] = {
            "id": i,
            "realm_id": 9,
            "user_id": 1000 + i,
            "name": f"M{i}",
            "pact_id": pact["id"],
            "cover_allies": False,
        }
    assert len([f for f in fiefs.values() if f.get("pact_id") == pact["id"]]) == B.PACT_SIZE_MAX
    try:
        engine.invite_to_pact(1, 2)
        raise AssertionError("expected full")
    except ValueError as e:
        assert "полон" in str(e).lower()


def test_guide_mentions_pact_consent():
    from app.domain.guide import game_guide

    text = game_guide()
    assert "согласия" in text.lower() or "приглашение" in text.lower()
