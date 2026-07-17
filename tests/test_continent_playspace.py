"""Континент как единое игровое пространство: долины без портальных ворот."""
from __future__ import annotations

from contextlib import nullcontext
from unittest.mock import MagicMock

from app import balance as B
from app.database import Database
from app.engine import Engine


def test_realms_are_adjacent_same_world_any_chain():
    db = MagicMock()
    db.get_realm.side_effect = lambda rid: {
        1: {"id": 1, "world_id": 9, "chain_index": 0},
        2: {"id": 2, "world_id": 9, "chain_index": 5},
        3: {"id": 3, "world_id": 8, "chain_index": 0},
    }.get(int(rid))

    assert Database.realms_are_adjacent(db, 1, 2) is True
    assert Database.realms_are_adjacent(db, 1, 1) is True
    assert Database.realms_are_adjacent(db, 1, 3) is False


def test_list_raid_targets_includes_far_valley():
    db = MagicMock()
    atk = {"id": 1, "realm_id": 1, "user_id": 10, "frozen": False, "name": "A"}
    local = {"id": 2, "realm_id": 1, "user_id": 20, "frozen": False, "name": "B"}
    far = {"id": 3, "realm_id": 3, "user_id": 30, "frozen": False, "name": "C"}
    db.get_fief.return_value = atk
    db.list_adjacent_realms.return_value = [
        {"id": 2, "chain_index": 1},
        {"id": 3, "chain_index": 2},
    ]

    def list_fiefs(rid):
        return {
            1: [atk, local],
            2: [],
            3: [far],
        }[int(rid)]

    db.list_fiefs.side_effect = list_fiefs
    engine = Engine(db)
    targets = engine.list_raid_target_fiefs(1)
    ids = {int(t["id"]) for t in targets}
    assert ids == {2, 3}
    far_item = next(t for t in targets if int(t["id"]) == 3)
    assert far_item["via_portal"] is True


def test_declare_caravan_allows_far_same_world():
    db = MagicMock()
    db.transaction = lambda: nullcontext()
    sender = {
        "id": 1,
        "realm_id": 1,
        "grain": 50,
        "goods": 10,
        "frozen": False,
        "name": "A",
        "user_id": 100,
    }
    receiver = {
        "id": 2,
        "realm_id": 3,
        "grain": 5,
        "goods": 5,
        "frozen": False,
        "name": "B",
        "user_id": 200,
    }
    fiefs = {1: sender, 2: receiver}

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

    db.get_fief.side_effect = get_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.create_action_intent.return_value = {"id": 3, "kind": "caravan"}
    db.realms_are_adjacent.return_value = True
    db.get_realm.return_value = {"id": 1, "world_id": 9, "tick_index": 2}
    engine = Engine(db)
    engine.require_active_fief = MagicMock(side_effect=get_fief)
    engine.collect_for_fief = MagicMock()
    engine.barn_level = MagicMock(return_value=0)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])
    engine._world_id_for_realm = MagicMock(return_value=9)
    engine._require_cross_valley_caught_up = MagicMock()

    result = engine.declare_caravan(1, 2, B.RES_GRAIN, 10)
    assert sender["grain"] == 40
    assert receiver["grain"] == 5
    assert result.receiver_name == "B"
    assert "B" in result.dm_text
    db.create_action_intent.assert_called_once()


def test_invite_to_pact_allows_other_valley_same_world():
    founder = {
        "id": 1,
        "realm_id": 1,
        "user_id": 101,
        "name": "Основатель",
        "pact_id": 50,
    }
    target = {
        "id": 2,
        "realm_id": 3,
        "user_id": 202,
        "name": "Гость",
        "pact_id": None,
    }
    pact = {"id": 50, "realm_id": 1, "name": "Север", "founder_fief_id": 1}
    db = MagicMock()
    db.get_fief.side_effect = lambda fid: {1: founder, 2: target}.get(fid)
    db.get_pact.return_value = pact
    db.pact_members.return_value = [founder]
    db.get_open_pact_invite.return_value = None
    db.get_realm.return_value = {"id": 1, "tick_index": 5}
    db.realms_are_adjacent.return_value = True
    db.create_pact_invite.return_value = {"id": 9}

    engine = Engine(db)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    invite = engine.invite_to_pact(1, 2)
    assert invite["id"] == 9
    db.create_pact_invite.assert_called_once()

