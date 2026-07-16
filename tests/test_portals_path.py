"""Линейная вставка порталов."""
from __future__ import annotations

from random import Random

from app.domain.portals import (
    insert_chain_index,
    is_adjacent,
    neighbor_chain_indices,
    pick_portal_insertion,
)


def test_is_adjacent_path():
    assert is_adjacent(0, 1)
    assert is_adjacent(2, 1)
    assert not is_adjacent(0, 2)
    assert not is_adjacent(None, 1)


def test_insert_before_and_after():
    assert insert_chain_index(existing_count=3, anchor_chain_index=1, side="before") == 1
    assert insert_chain_index(existing_count=3, anchor_chain_index=1, side="after") == 2


def test_pick_portal_insertion_solo():
    anchor, side, new_index = pick_portal_insertion([], Random(0))
    assert new_index == 0


def test_neighbor_ends():
    assert neighbor_chain_indices(0, path_len=4) == [1]
    assert neighbor_chain_indices(3, path_len=4) == [2]
    assert neighbor_chain_indices(1, path_len=4) == [0, 2]
