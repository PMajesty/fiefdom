"""Critical #5: one fief per world + shared clock_mode (no temp-realm gameplay)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import balance as B
from app.database import Database
from app.engine import Engine
from app.handlers.shared import resolve_fief_for_user, resolve_realm_for_user


def _join_ready_db(*, world_id: int = 1, owned_world_fief=None):
    db = MagicMock()
    db.get_fief_by_user.return_value = None
    db.get_fief_by_user_world.return_value = owned_world_fief
    tile = {
        "id": 50,
        "x": 1,
        "y": 2,
        "tile_type": B.TILE_FIELD,
        "owner_fief_id": None,
    }
    db.get_tile_by_id.return_value = tile
    db.get_realm.return_value = {
        "id": 2,
        "width": 6,
        "height": 6,
        "world_id": world_id,
    }
    db.get_tiles.return_value = [tile]
    db.create_fief.return_value = {"id": 7, "name": "Усадьба @ivan", "world_id": world_id}
    db.claim_unowned_tile.return_value = {
        **tile,
        "owner_fief_id": 7,
        "building": B.BLD_MANOR,
        "building_level": B.STARTING_MANOR_LEVEL,
        "is_core": True,
    }
    tx = MagicMock()
    tx.__enter__ = MagicMock(return_value=None)
    tx.__exit__ = MagicMock(return_value=False)
    db.transaction.return_value = tx
    return db


def test_clock_mode_migration_defaults_shared():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.side_effect = [
        (1,),  # existing world
        (0,),  # need_attach
        (0,),  # fiefs without world_id
        None,  # duplicate check (fetchall used)
    ]
    cursor.fetchall.return_value = []

    try:
        db._ensure_world_schema()
    except Exception:
        pass

    executed = [" ".join(c[0][0].split()) for c in cursor.execute.call_args_list]
    assert any(
        "ADD COLUMN IF NOT EXISTS clock_mode" in sql and "DEFAULT 'shared'" in sql
        for sql in executed
    )
    assert any("idx_fiefs_user_world" in sql and "user_id, world_id" in sql for sql in executed)
    assert any("DROP INDEX IF EXISTS idx_fiefs_user_id" in sql for sql in executed)
    assert not any(
        sql.strip().startswith("CREATE UNIQUE INDEX") and "idx_fiefs_user_id" in sql
        for sql in executed
    )


def test_fief_world_migration_fails_loud_on_null_world_id():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.side_effect = [
        (1,),  # existing world
        (0,),  # need_attach
        (3,),  # null world_id fiefs
    ]
    cursor.fetchall.return_value = []

    with pytest.raises(RuntimeError, match="without world_id"):
        db._ensure_world_schema()


def test_fief_world_migration_fails_loud_on_duplicates():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.side_effect = [
        (1,),  # existing world
        (0,),  # need_attach
        (0,),  # null world_id count
    ]
    cursor.fetchall.return_value = [(42, 1, 2)]

    with pytest.raises(RuntimeError, match="duplicate \\(user_id, world_id\\)"):
        db._ensure_world_schema()


def test_sync_realms_clock_filters_shared_only():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor

    db.sync_realms_clock_from_world(9)

    sql = " ".join(cursor.execute.call_args[0][0].split())
    assert "realms.clock_mode = 'shared'" in sql
    assert "realms.world_id = w.id" in sql
    assert cursor.execute.call_args[0][1] == (9,)


def test_create_fief_inserts_denormalized_world_id():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.side_effect = [
        (12, 3),  # tick_index, world_id
        (1, 10, 3, "Manor"),  # RETURNING row
    ]
    cursor.description = [
        ("id",),
        ("realm_id",),
        ("world_id",),
        ("name",),
    ]

    row = db.create_fief(10, 42, "Manor", grain=1, goods=2, might=3, actions=1)

    insert_sql = " ".join(cursor.execute.call_args_list[1][0][0].split())
    assert "world_id" in insert_sql
    assert cursor.execute.call_args_list[1][0][1][2] == 3
    assert row["world_id"] == 3


def test_create_fief_rejects_realm_without_world():
    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    db.cursor = cursor
    cursor.fetchone.return_value = (0, None)

    with pytest.raises(ValueError, match="не привязана к миру"):
        db.create_fief(10, 42, "Manor")


def test_join_fief_rejects_second_fief_same_world():
    db = _join_ready_db(
        world_id=1,
        owned_world_fief={"id": 9, "realm_id": 1, "user_id": 100, "world_id": 1},
    )
    engine = Engine(db)
    engine.ensure_user = MagicMock()
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    with pytest.raises(ValueError, match="уже есть усадьба на континенте"):
        engine.join_fief(2, user, tile_id=50)
    db.create_fief.assert_not_called()


def test_join_fief_allows_second_fief_different_world():
    db = _join_ready_db(world_id=2, owned_world_fief=None)
    engine = Engine(db)
    engine.ensure_user = MagicMock()
    engine.maybe_grow_map = MagicMock(return_value=None)
    user = SimpleNamespace(id=100, full_name="Иван", first_name="Иван", username="ivan")

    fief, _msg = engine.join_fief(2, user, tile_id=50)

    assert fief["id"] == 7
    db.get_fief_by_user_world.assert_called_once_with(100, 2)
    db.create_fief.assert_called_once()


def test_has_fief_elsewhere_is_world_scoped():
    db = MagicMock()
    db.get_realm.return_value = {"id": 2, "world_id": 1}
    db.get_fief_by_user_world.return_value = {
        "id": 9,
        "realm_id": 1,
        "world_id": 1,
    }
    engine = Engine(db)
    assert engine.has_fief_elsewhere(42, 2) is True

    db.get_fief_by_user_world.return_value = {
        "id": 9,
        "realm_id": 2,
        "world_id": 1,
    }
    assert engine.has_fief_elsewhere(42, 2) is False

    db.get_realm.return_value = {"id": 3, "world_id": 9}
    db.get_fief_by_user_world.return_value = None
    assert engine.has_fief_elsewhere(42, 3) is False


def test_resolve_fief_for_user_realm_scoped_still_works():
    engine = MagicMock()
    engine.db.get_fief_by_user.return_value = {"id": 7, "realm_id": 1, "user_id": 42}
    found = resolve_fief_for_user(engine, 42, realm_id=1)
    assert found["id"] == 7
    engine.db.get_fief_by_user.assert_called_once_with(1, 42)


def test_resolve_fief_for_user_single_holding_fallback():
    engine = MagicMock()
    engine.db.get_user.return_value = {"telegram_id": 42, "last_realm_id": None}
    engine.db.get_realm_by_chat.return_value = None
    engine.db.get_realm.return_value = {"id": 1, "world_id": 1}
    engine.db.list_fiefs_by_user.return_value = [
        {"id": 7, "realm_id": 1, "user_id": 42, "world_id": 1}
    ]
    engine.db.get_fief_by_user.return_value = {
        "id": 7,
        "realm_id": 1,
        "user_id": 42,
        "world_id": 1,
    }
    found = resolve_fief_for_user(engine, 42)
    assert found["id"] == 7
    engine.db.get_fief_by_user.assert_called_with(1, 42)


def test_resolve_recovers_from_poisoned_last_realm():
    """last_realm на чужой долине не должен ронять /меню при одной усадьбе."""
    owned = {"id": 7, "realm_id": 1, "user_id": 42, "world_id": 1}
    engine = MagicMock()
    engine.db.get_user.return_value = {"telegram_id": 42, "last_realm_id": 2}
    engine.db.get_realm.side_effect = lambda rid: (
        {"id": 2, "world_id": 1} if int(rid) == 2 else {"id": 1, "world_id": 1}
    )
    engine.db.get_fief_by_user.side_effect = lambda rid, uid: (
        owned if int(rid) == 1 else None
    )
    engine.db.list_fiefs_by_user.return_value = [owned]

    realm = resolve_realm_for_user(engine, 42)
    assert realm is not None and int(realm["id"]) == 1
    engine.db.set_last_realm.assert_called_once_with(42, 1)

    found = resolve_fief_for_user(engine, 42)
    assert found["id"] == 7


@pytest.mark.asyncio
async def test_cmd_start_realm_foreign_redirect_sets_owned_last_realm():
    """realm_ deep-link на чужую долину: last_realm = своя, не foreign."""
    from aiogram.filters import CommandObject

    from app.handlers import dm as dm_mod

    owned = {"id": 7, "realm_id": 1, "user_id": 100, "world_id": 1}
    engine = MagicMock()
    engine.ensure_user = MagicMock()
    engine.get_realm.return_value = {"id": 2, "title": "Чужая", "world_id": 1}
    engine.fief_of_user_in_realm.return_value = None
    engine.fief_of_user_in_world.return_value = owned

    message = MagicMock()
    message.from_user = MagicMock(id=100)
    command = CommandObject(prefix="/", command="start", args="realm_2")

    with (
        patch.object(dm_mod, "get_engine", return_value=engine),
        patch.object(dm_mod, "answer_html", new_callable=AsyncMock) as answer,
        patch.object(dm_mod, "show_status", new_callable=AsyncMock) as status,
    ):
        await dm_mod.cmd_start(message, command)

    engine.get_realm.assert_called_once_with(2)
    engine.fief_of_user_in_realm.assert_called_once_with(100, 2)
    engine.fief_of_user_in_world.assert_called_once_with(100, 1)
    engine.remember_last_realm.assert_called_once_with(100, 1)
    answer.assert_awaited_once()
    assert "уже есть усадьба" in answer.await_args.args[1]
    status.assert_awaited_once_with(message, 7)


@pytest.mark.asyncio
async def test_cmd_start_realm_owned_sets_last_realm_after_check():
    from aiogram.filters import CommandObject

    from app.handlers import dm as dm_mod

    fief = {"id": 7, "realm_id": 1, "user_id": 100, "world_id": 1}
    engine = MagicMock()
    engine.ensure_user = MagicMock()
    engine.get_realm.return_value = {"id": 1, "title": "Дом", "world_id": 1}
    engine.fief_of_user_in_realm.return_value = fief

    message = MagicMock()
    message.from_user = MagicMock(id=100)
    command = CommandObject(prefix="/", command="start", args="realm_1")

    with (
        patch.object(dm_mod, "get_engine", return_value=engine),
        patch.object(dm_mod, "show_status", new_callable=AsyncMock) as status,
    ):
        await dm_mod.cmd_start(message, command)

    engine.get_realm.assert_called_once_with(1)
    engine.fief_of_user_in_realm.assert_called_once_with(100, 1)
    engine.remember_last_realm.assert_called_once_with(100, 1)
    status.assert_awaited_once_with(message, 7)


def test_require_owned_fief_returns_owned():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    db = MagicMock()
    db.get_fief.return_value = fief
    engine = Engine(db)
    assert engine.require_owned_fief(7, 100) is fief
    db.get_fief.assert_called_once_with(7)


def test_require_owned_fief_rejects_foreign_or_missing():
    db = MagicMock()
    db.get_fief.return_value = {"id": 7, "user_id": 999, "realm_id": 3}
    engine = Engine(db)
    with pytest.raises(ValueError, match="не ваша усадьба"):
        engine.require_owned_fief(7, 100)

    db.get_fief.return_value = None
    with pytest.raises(ValueError, match="не ваша усадьба"):
        engine.require_owned_fief(7, 100)


def test_require_owned_active_fief_checks_active_play():
    fief = {"id": 7, "user_id": 100, "realm_id": 3}
    db = MagicMock()
    db.get_fief.return_value = fief
    engine = Engine(db)
    with patch.object(engine, "fief_is_active_play", return_value=True):
        assert engine.require_owned_active_fief(7, 100) is fief
    with patch.object(engine, "fief_is_active_play", return_value=False):
        with pytest.raises(ValueError, match="активной"):
            engine.require_owned_active_fief(7, 100)
