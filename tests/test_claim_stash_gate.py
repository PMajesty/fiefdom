"""Soft-gate занятия по stash_cap и промпт UI."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import balance as B
from app.handlers.dm import claim_prompt_text
from app.services.land_actions import LandActionService


def _claim_service(*, barn: int, goods: int, tiles_n: int, tile_type: str):
    fief = {
        "id": 1,
        "realm_id": 9,
        "goods": goods,
        "actions": 2,
        "onboard_step": 4,
    }
    owned = [
        {"id": i, "x": 0, "y": i, "is_overgrown": False, "owner_fief_id": 1}
        for i in range(tiles_n)
    ]
    target = {
        "id": 99,
        "x": 1,
        "y": 0,
        "tile_type": tile_type,
        "owner_fief_id": None,
        "is_overgrown": False,
        "ruins_looted": False,
    }
    db = MagicMock()
    db.get_fief.return_value = fief
    db.fief_tiles.return_value = owned
    db.get_tile.return_value = target
    db.get_realm.return_value = {"width": 6, "height": 6}
    db.debit_fief_resources.return_value = fief
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx

    engine = MagicMock()
    engine.barn_level.return_value = barn
    engine.tile_views.return_value = []
    engine.collect_for_fief.return_value = []
    return LandActionService(engine, db), db, engine


def test_claim_blocked_when_cost_above_stash_cap():
    # 4 клетки → следующая №5 стоит 175, без амбара cap=150
    svc, db, _engine = _claim_service(
        barn=0, goods=500, tiles_n=4, tile_type=B.TILE_FIELD
    )
    with patch(
        "app.services.land_actions.adjacent_claimable",
        return_value={(1, 0)},
    ):
        with pytest.raises(ValueError, match=B.CLAIM_STASH_TOO_SMALL):
            svc.claim_tile(1, 1, 0)
    db.debit_fief_resources.assert_not_called()


def test_claim_allowed_when_barn_covers_cost():
    svc, db, _engine = _claim_service(
        barn=1, goods=500, tiles_n=4, tile_type=B.TILE_FIELD
    )
    with patch(
        "app.services.land_actions.adjacent_claimable",
        return_value={(1, 0)},
    ):
        msg = svc.claim_tile(1, 1, 0)
    assert "присоединена" in msg
    db.debit_fief_resources.assert_called()


def test_wilds_claim_same_cost_as_field():
    assert B.claim_cost(5, is_wilds=True) == B.claim_cost(5, is_wilds=False)


def test_claim_prompt_appends_stash_hint():
    engine = MagicMock()
    engine.barn_level.return_value = 0
    fief = {"id": 3}
    tile_meta = {(1, 0): (B.TILE_FIELD, False)}
    text = claim_prompt_text(
        engine, fief, 5, tile_meta, base="Выберите клетку:"
    )
    assert text.startswith("Выберите клетку:")
    assert B.CLAIM_STASH_TOO_SMALL in text
    assert "175" in text


def test_claim_prompt_clear_when_barn_enough():
    engine = MagicMock()
    engine.barn_level.return_value = 1
    fief = {"id": 3}
    tile_meta = {(1, 0): (B.TILE_FIELD, False)}
    text = claim_prompt_text(
        engine, fief, 5, tile_meta, base="Выберите клетку:"
    )
    assert text == "Выберите клетку:"
