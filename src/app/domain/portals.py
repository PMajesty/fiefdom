"""Линейная сеть порталов: путь по chain_index, степень узла не больше 2."""
from __future__ import annotations

import random
from typing import Sequence


def is_adjacent(chain_a: int | None, chain_b: int | None) -> bool:
    if chain_a is None or chain_b is None:
        return False
    return abs(int(chain_a) - int(chain_b)) == 1


def insert_chain_index(
    *,
    existing_count: int,
    anchor_chain_index: int,
    side: str,
) -> int:
    """Индекс для новой долины рядом с якорем. side: before|after."""
    if existing_count <= 0:
        return 0
    anchor = int(anchor_chain_index)
    if side == "before":
        return anchor
    return anchor + 1


def pick_portal_insertion(
    chain_indices: Sequence[int],
    rng: random.Random | None = None,
) -> tuple[int, str, int]:
    """Выбирает якорь и сторону. Возвращает (anchor_index, side, new_index)."""
    rng = rng or random.Random()
    if not chain_indices:
        return 0, "after", 0
    ordered = sorted(int(i) for i in chain_indices)
    anchor = rng.choice(ordered)
    side = rng.choice(("before", "after"))
    new_index = insert_chain_index(
        existing_count=len(ordered),
        anchor_chain_index=anchor,
        side=side,
    )
    return anchor, side, new_index


def neighbor_chain_indices(chain_index: int, *, path_len: int) -> list[int]:
    idx = int(chain_index)
    out: list[int] = []
    if idx > 0:
        out.append(idx - 1)
    if idx + 1 < path_len:
        out.append(idx + 1)
    return out
