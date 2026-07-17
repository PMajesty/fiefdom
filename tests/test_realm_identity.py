"""Phase 5: realm/world identity substrate + join CAS."""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import balance as B
from app.database import Database
from app.domain.realm_identity import (
    CLOCK_MODE_INDEPENDENT,
    CLOCK_MODE_SHARED,
    REALM_KIND_EXPEDITION,
    REALM_KIND_VALLEY,
    WORLD_KIND_CONTINENT,
    WORLD_KIND_INSTANCE,
    feature_flag_enabled,
    is_continent_world,
    is_instance_world,
    normalize_clock_mode,
    normalize_realm_kind,
    normalize_world_kind,
    realm_expired,
    second_fief_on_world_message,
    shares_continent_clock,
    world_expired,
)
from app.engine import Engine


def _sql(call) -> str:
    return " ".join(call[0][0].split()).lower()


def test_identity_helpers():
    assert normalize_world_kind(None) == WORLD_KIND_CONTINENT
    assert normalize_world_kind("instance") == WORLD_KIND_INSTANCE
    assert normalize_realm_kind(None) == REALM_KIND_VALLEY
    assert normalize_realm_kind("expedition") == REALM_KIND_EXPEDITION
    assert normalize_clock_mode(None) == CLOCK_MODE_SHARED
    assert normalize_clock_mode("independent") == CLOCK_MODE_INDEPENDENT
    assert is_continent_world({"world_kind": "continent"})
    assert is_instance_world({"world_kind": "instance"})
    assert shares_continent_clock({"clock_mode": "shared"})
    assert not shares_continent_clock({"clock_mode": "independent"})
    assert not world_expired({"expires_tick": None}, tick_index=10)
    assert world_expired({"expires_tick": 10}, tick_index=10)
    assert not realm_expired({"expires_tick": 11}, tick_index=10)
    assert feature_flag_enabled({"feature_flags": {"relics": True}}, "relics")
    assert not feature_flag_enabled({"feature_flags": {}}, "relics")
    assert second_fief_on_world_message() == (
        "У вас уже есть усадьба на континенте. "
        "Вторая усадьба недоступна."
    )


def test_identity_ddl_in_world_schema_source():
    from app import database as database_mod

    src = inspect.getsource(database_mod.Database._ensure_world_schema)
    assert "world_kind" in src
    assert "parent_world_id" in src
    assert "realm_kind" in src
    assert "expires_tick" in src


def test_create_instance_world_sql():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.return_value = (9, "Raid", "instance", 1, 40, "Europe/Moscow")
    cursor.description = [
        ("id",),
        ("name",),
        ("world_kind",),
        ("parent_world_id",),
        ("expires_tick",),
        ("timezone",),
    ]
    row = db.create_instance_world(
        name="Raid", parent_world_id=1, expires_tick=40
    )
    assert row["world_kind"] == "instance"
    sql = _sql(cursor.execute.call_args)
    assert "insert into worlds" in sql
    assert "'instance'" in sql or "instance" in sql
    assert cursor.execute.call_args[0][1][1] == 1


def test_claim_unowned_tile_cas_sql():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.return_value = (50, 7)
    cursor.description = [("id",), ("owner_fief_id",)]
    row = db.claim_unowned_tile(
        50, 3, owner_fief_id=7, building=B.BLD_MANOR, building_level=1, is_core=True
    )
    assert row["owner_fief_id"] == 7
    sql = _sql(cursor.execute.call_args)
    assert "owner_fief_id is null" in sql
    assert "returning *" in sql
    cursor.fetchone.return_value = None
    assert db.claim_unowned_tile(50, 3, owner_fief_id=8) is None


def test_join_fief_uses_cas_claim_inside_transaction():
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db.get_fief_by_user_world.return_value = None
    db.get_realm.return_value = {
        "id": 2,
        "width": 6,
        "height": 6,
        "world_id": 1,
    }
    db.get_tile_by_id.return_value = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_FIELD,
        "owner_fief_id": None,
    }
    db.get_tiles.return_value = [
        {"id": 50, "x": 1, "y": 2, "tile_type": B.TILE_FIELD, "owner_fief_id": None},
    ]
    db.create_fief.return_value = {"id": 7, "name": "Усадьба @ivan", "world_id": 1}
    db.claim_unowned_tile.return_value = {
        "id": 50,
        "owner_fief_id": 7,
        "building": B.BLD_MANOR,
    }
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx

    engine = Engine(db)
    engine.ensure_user = MagicMock()
    engine.maybe_grow_map = MagicMock(return_value=None)
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    fief, msg = engine.join_fief(2, user, tile_id=50)
    assert fief["id"] == 7
    db.transaction.assert_called_once()
    db.claim_unowned_tile.assert_called_once()
    db.update_tile.assert_not_called()
    assert "основана" in msg


def test_join_fief_cas_miss_raises_unavailable():
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db.get_fief_by_user_world.return_value = None
    db.get_realm.return_value = {"id": 2, "width": 6, "height": 6, "world_id": 1}
    db.get_tile_by_id.return_value = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_FIELD,
        "owner_fief_id": None,
    }
    db.get_tiles.return_value = [
        {"id": 50, "x": 1, "y": 2, "tile_type": B.TILE_FIELD, "owner_fief_id": None},
    ]
    db.create_fief.return_value = {"id": 7, "name": "Усадьба", "world_id": 1}
    db.claim_unowned_tile.return_value = None
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx

    engine = Engine(db)
    engine.ensure_user = MagicMock()
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    with pytest.raises(ValueError, match="Клетка недоступна"):
        engine.join_fief(2, user, tile_id=50)
