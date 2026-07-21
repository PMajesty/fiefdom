"""Рёбра realm_links: степень ≤ MAX, seed-путь, play остаётся continent-wide."""
from __future__ import annotations

from contextlib import nullcontext
from random import Random
from unittest.mock import MagicMock

import pytest

from app import balance as B
from app.database import Database
from app.domain.portals import (
    ordered_link_pair,
    path_edges_for_seed,
    pick_link_anchor,
)
from app.services.realm_admin import RealmLifecycleService


def test_ordered_link_pair_sorted():
    assert ordered_link_pair(5, 2) == (2, 5)
    assert ordered_link_pair(2, 5) == (2, 5)


def test_ordered_link_pair_rejects_self():
    with pytest.raises(ValueError):
        ordered_link_pair(3, 3)


def test_path_edges_for_seed():
    assert path_edges_for_seed([10]) == []
    assert path_edges_for_seed([10, 20, 5]) == [(10, 20), (5, 20)]


def test_pick_link_anchor_prefers_under_max_degree():
    # Все с degree 3 кроме id=7
    candidates = [(1, 3), (7, 1), (9, 3)]
    picked = {
        pick_link_anchor(candidates, max_degree=3, rng=Random(i)) for i in range(20)
    }
    assert picked == {7}


def test_pick_link_anchor_rejects_when_saturated():
    candidates = [(1, 3), (2, 3), (3, 3)]
    with pytest.raises(ValueError, match="степенью"):
        pick_link_anchor(candidates, max_degree=3, rng=Random(0))


def test_max_realm_neighbors_constant():
    assert B.MAX_REALM_NEIGHBORS == 3


def test_play_adjacency_still_same_world():
    """Prep не переключает play на рёбра."""
    db = MagicMock()
    db.get_realm.side_effect = lambda rid: {
        1: {"id": 1, "world_id": 9, "chain_index": 0},
        2: {"id": 2, "world_id": 9, "chain_index": 5},
        3: {"id": 3, "world_id": 8, "chain_index": 0},
    }.get(int(rid))

    assert Database.realms_are_adjacent(db, 1, 2) is True
    assert Database.realms_are_adjacent(db, 1, 3) is False


def test_seed_realm_links_skips_when_edge_exists():
    db = Database.__new__(Database)
    db.cursor = MagicMock()
    db.cursor.fetchall.side_effect = [
        [(1,)],  # worlds
        [(10,), (20,)],  # realms
    ]
    db.cursor.fetchone.return_value = (1,)  # existing link count

    Database._seed_realm_links_if_empty(db)

    insert_sqls = [
        c.args[0]
        for c in db.cursor.execute.call_args_list
        if c.args and "INSERT INTO realm_links" in c.args[0]
    ]
    assert insert_sqls == []


def test_seed_realm_links_inserts_path_when_empty():
    db = Database.__new__(Database)
    db.cursor = MagicMock()
    db.cursor.fetchall.side_effect = [
        [(1,)],  # worlds
        [(10,), (20,), (5,)],  # realms by chain
    ]
    db.cursor.fetchone.return_value = (0,)

    Database._seed_realm_links_if_empty(db)

    inserts = [
        c.args[1]
        for c in db.cursor.execute.call_args_list
        if c.args and "INSERT INTO realm_links" in c.args[0]
    ]
    assert inserts == [(10, 20), (5, 20)]


def test_seed_realm_links_noop_for_single_realm():
    db = Database.__new__(Database)
    db.cursor = MagicMock()
    db.cursor.fetchall.side_effect = [
        [(1,)],
        [(10,)],
    ]

    Database._seed_realm_links_if_empty(db)

    insert_sqls = [
        c.args[0]
        for c in db.cursor.execute.call_args_list
        if c.args and "INSERT INTO realm_links" in c.args[0]
    ]
    assert insert_sqls == []


def test_list_realm_link_degrees_counts_both_ends():
    db = MagicMock()
    db.list_realms_by_chain.return_value = [
        {"id": 1},
        {"id": 2},
        {"id": 3},
    ]
    db._fetchall.return_value = [
        {"realm_low": 1, "realm_high": 2},
        {"realm_low": 2, "realm_high": 3},
    ]

    degrees = Database.list_realm_link_degrees(db, 9)
    assert degrees == {1: 1, 2: 2, 3: 1}


def _world_row() -> dict:
    return {
        "id": 1,
        "timezone": "Europe/Moscow",
        "tick_index": 0,
        "day_number": 1,
        "next_catastrophe_tick": 5,
        "next_catastrophe_key": "storm",
        "last_tick_local_date": None,
        "last_tick_slot": None,
        "pending_minor_key": None,
        "active_minor_key": None,
    }


def test_create_realm_first_has_no_link(monkeypatch):
    db = MagicMock()
    db.get_realm_by_chat.return_value = None
    world = _world_row()
    db.get_or_create_world.return_value = world
    db.get_world.return_value = world
    db.list_realms_by_chain.return_value = []
    db.transaction = lambda: nullcontext()
    created = {"id": 1, "title": "Первая", "day_number": 1, "chat_id": -1}
    db.create_realm.return_value = created
    db.get_realm.return_value = created
    monkeypatch.setattr("app.services.realm_admin.generate_map", lambda w, h: [])
    monkeypatch.setattr("app.services.realm_admin.best_rectangle", lambda n: (6, 6))

    svc = RealmLifecycleService(engine=MagicMock(), db=db)
    svc.create_realm(-1, "Первая", creator_user_id=1)

    db.lock_world_realms_for_links.assert_called_once_with(1)
    db.update_world.assert_called_once()
    db.ensure_realm_link.assert_not_called()


def test_create_realm_links_to_under_degree_anchor(monkeypatch):
    db = MagicMock()
    db.get_realm_by_chat.return_value = None
    db.get_or_create_world.return_value = _world_row()
    db.get_world.return_value = db.get_or_create_world.return_value
    peers = [
        {"id": 10, "title": "Старая", "chain_index": 0},
        {"id": 11, "title": "Полная", "chain_index": 1},
    ]
    db.list_realms_by_chain.return_value = peers
    db.list_realm_link_degrees.return_value = {10: 1, 11: 3}
    db.transaction = lambda: nullcontext()
    created = {
        "id": 99,
        "title": "Новая",
        "day_number": 1,
        "chat_id": -100,
    }
    db.create_realm.return_value = created
    db.get_realm.return_value = created

    monkeypatch.setattr(
        "app.services.realm_admin.generate_map",
        lambda w, h: [],
    )
    monkeypatch.setattr(
        "app.services.realm_admin.best_rectangle",
        lambda n: (6, 6),
    )
    monkeypatch.setattr(
        "app.services.realm_admin.random.Random",
        lambda: Random(0),
    )

    svc = RealmLifecycleService(engine=MagicMock(), db=db)
    realm, msg = svc.create_realm(-100, "Новая", creator_user_id=1)

    assert int(realm["id"]) == 99
    db.lock_world_realms_for_links.assert_called_once_with(1)
    db.ensure_realm_link.assert_called_once_with(99, 10)
    assert "Старая" in msg
    assert str(B.MAX_REALM_NEIGHBORS) in msg


def test_create_realm_links_when_peers_visible_only_after_lock(monkeypatch):
    """После FOR UPDATE видит уже созданную долину и не сбрасывает часы мира."""
    db = MagicMock()
    db.get_realm_by_chat.return_value = None
    db.get_or_create_world.return_value = _world_row()
    db.get_world.return_value = db.get_or_create_world.return_value
    peer = {"id": 10, "title": "Уже есть", "chain_index": 0}
    db.list_realms_by_chain.return_value = [peer]
    db.list_realm_link_degrees.return_value = {10: 0}
    db.transaction = lambda: nullcontext()
    created = {"id": 99, "title": "Новая", "day_number": 1, "chat_id": -2}
    db.create_realm.return_value = created
    db.get_realm.return_value = created
    monkeypatch.setattr("app.services.realm_admin.generate_map", lambda w, h: [])
    monkeypatch.setattr("app.services.realm_admin.best_rectangle", lambda n: (6, 6))
    monkeypatch.setattr("app.services.realm_admin.random.Random", lambda: Random(0))

    svc = RealmLifecycleService(engine=MagicMock(), db=db)
    _realm, msg = svc.create_realm(-2, "Новая", creator_user_id=1)

    db.lock_world_realms_for_links.assert_called_once_with(1)
    db.ensure_realm_link.assert_called_once_with(99, 10)
    db.update_world.assert_not_called()
    assert "Уже есть" in msg
