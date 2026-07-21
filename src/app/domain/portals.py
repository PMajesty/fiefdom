"""Топология долин: рёбра realm_links (степень ≤ MAX) и мягкий порядок chain_index."""
from __future__ import annotations

import random
from typing import Sequence

from app import balance as B


def ordered_link_pair(realm_a: int, realm_b: int) -> tuple[int, int]:
    a, b = int(realm_a), int(realm_b)
    if a == b:
        raise ValueError("Нельзя связать долину саму с собой")
    return (a, b) if a < b else (b, a)


def is_adjacent(chain_a: int | None, chain_b: int | None) -> bool:
    """Соседство по линейному chain_index (legacy / тесты порядка)."""
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
    """Выбирает якорь и сторону для chain_index. Возвращает (anchor_index, side, new_index)."""
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


def pick_link_anchor(
    candidates: Sequence[tuple[int, int]],
    *,
    max_degree: int | None = None,
    rng: random.Random | None = None,
) -> int | None:
    """Якорь для нового ребра среди узлов со степенью < max.

    Create добавляет одно ребро к дереву - при max≥2 всегда есть under-degree.
    Пустой список → None; все на потолке → ValueError (жёсткий MAX).
    """
    rng = rng or random.Random()
    cap = int(B.MAX_REALM_NEIGHBORS if max_degree is None else max_degree)
    rows = [(int(rid), int(deg)) for rid, deg in candidates]
    if not rows:
        return None
    under = [rid for rid, deg in rows if deg < cap]
    if not under:
        raise ValueError(
            f"Нет долины со степенью < {cap} для нового портала"
        )
    return rng.choice(under)


def path_edges_for_seed(ordered_realm_ids: Sequence[int]) -> list[tuple[int, int]]:
    """Рёбра пути по порядку (для одноразового seed без существующих links)."""
    ids = [int(x) for x in ordered_realm_ids]
    if len(ids) < 2:
        return []
    return [ordered_link_pair(ids[i], ids[i + 1]) for i in range(len(ids) - 1)]
