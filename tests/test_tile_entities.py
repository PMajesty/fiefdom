"""Phase 3: tile_entities schema, CRUD, registry contracts, tick/render/modifier seams."""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.database import Database
from app.domain.production import TileView

from app.rendering.map_image import map_fingerprint, render_map_image
from app.domain.modifiers import (
    EffectKind,
    ModifierScope,
    RealmModifierCtx,
    collect_active_modifiers,
)
from app.domain.tile_entities import (
    ENTITY_KIND_CONTRACTS,
    TICK_RESOLVE_HANDLERS,
    EntityKindContract,
    EntityModifierDecl,
    TileEntityResolveCtx,
    active_tile_entity_ref,
    entity_fingerprint_rows,
    entity_map_marks,
    resolve_realm_tile_entities,
    validate_entity_kind_contracts,
)
from app.engine import Engine


def _sql(cursor_execute_call) -> str:
    return " ".join(cursor_execute_call[0][0].split()).lower()


def _db_with_mock_conn() -> tuple[Database, MagicMock]:
    db = Database(connect=False)
    conn = MagicMock()
    db.connection = conn
    db.cursor = MagicMock()
    return db, conn


@contextmanager
def _fake_entity_kind(
    *,
    key: str = "_test_camp",
    map_mark: str | None = "!",
    farm_mult_field: str = "farm_mult",
    scope: ModifierScope = ModifierScope.REALM,
):
    """Временная регистрация test-only kind (не отгруженный контент)."""

    def _tick(row, ctx: TileEntityResolveCtx):
        payload = dict(row.get("payload") or {})
        ticks = int(payload.get("ticks_seen") or 0) + 1
        payload["ticks_seen"] = ticks
        ctx.update_entity(int(row["id"]), payload=payload)
        return [f"test-camp@{row['x']},{row['y']}:t{ticks}"]

    contract = EntityKindContract(
        key=key,
        has_tick_handler=True,
        map_mark=map_mark,
        modifiers=(
            EntityModifierDecl(
                payload_field=farm_mult_field,
                kind=EffectKind.FARM_MULT,
                scope=scope,
            ),
        ),
    )
    ENTITY_KIND_CONTRACTS[key] = contract
    TICK_RESOLVE_HANDLERS[key] = _tick
    try:
        yield key
    finally:
        ENTITY_KIND_CONTRACTS.pop(key, None)
        TICK_RESOLVE_HANDLERS.pop(key, None)


def test_tile_entities_ddl_is_additive_idempotent():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    db._ensure_world_schema = MagicMock()  # type: ignore[method-assign]
    db._apply_patch_annul_open_trades = MagicMock()  # type: ignore[method-assign]
    db._apply_patch_remap_tick_slots_2_to_4 = MagicMock()  # type: ignore[method-assign]

    db.create_tables()
    executed = [_sql(c) for c in cursor.execute.call_args_list]
    create_stmts = [s for s in executed if "create table if not exists tile_entities" in s]
    assert len(create_stmts) == 1
    ddl = create_stmts[0]
    assert "realm_id bigint not null references realms(id) on delete cascade" in ddl
    assert "kind text not null" in ddl
    assert "payload jsonb not null default '{}'" in ddl
    assert "status text not null default 'active'" in ddl
    assert "created_tick int not null" in ddl
    assert "expires_tick int" in ddl
    assert any(
        "create index if not exists idx_tile_entities_realm on tile_entities(realm_id)"
        in s
        for s in executed
    )
    assert any(
        "create index if not exists idx_tile_entities_realm_xy"
        in s
        and "tile_entities(realm_id, x, y)" in s
        for s in executed
    )


def test_create_tile_entity_sql_and_row_mapping():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (
        11,
        3,
        1,
        2,
        "_test_camp",
        '{"farm_mult": 0.9}',
        "active",
        5,
        8,
        None,
    )
    cursor.description = [
        ("id",),
        ("realm_id",),
        ("x",),
        ("y",),
        ("kind",),
        ("payload",),
        ("status",),
        ("created_tick",),
        ("expires_tick",),
        ("created_at",),
    ]
    row = db.create_tile_entity(
        realm_id=3,
        x=1,
        y=2,
        kind="_test_camp",
        payload={"farm_mult": 0.9},
        created_tick=5,
        expires_tick=8,
    )
    assert row["id"] == 11
    assert row["payload"] == {"farm_mult": 0.9}
    sql = _sql(cursor.execute.call_args)
    assert "insert into tile_entities" in sql
    assert "%s::jsonb" in sql
    assert cursor.execute.call_args[0][1][0] == 3
    assert cursor.execute.call_args[0][1][3] == "_test_camp"
    assert json.loads(cursor.execute.call_args[0][1][4]) == {"farm_mult": 0.9}


def test_list_active_and_at_tile_sql_shape():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchall.return_value = []
    cursor.description = [("id",)]

    db.list_active_tile_entities(9)
    sql = _sql(cursor.execute.call_args)
    assert "from tile_entities" in sql
    assert "realm_id=%s" in sql
    assert "status='active'" in sql
    assert cursor.execute.call_args[0][1] == (9,)

    db.list_tile_entities_at(9, 1, 2)
    sql = _sql(cursor.execute.call_args)
    assert "x=%s and y=%s" in sql
    assert "status='active'" in sql
    assert cursor.execute.call_args[0][1] == (9, 1, 2)


def test_claim_expire_and_delete_sql_are_cas_shaped():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (4, "expired")
    cursor.description = [("id",), ("status",)]

    row = db.claim_expire_tile_entity(4)
    assert row == {"id": 4, "status": "expired"}
    sql = _sql(cursor.execute.call_args)
    assert "update tile_entities set status='expired'" in sql
    assert "id=%s and status='active'" in sql
    assert "returning *" in sql

    cursor.fetchone.return_value = None
    assert db.claim_expire_tile_entity(4) is None

    db.delete_tile_entity(4)
    sql = _sql(cursor.execute.call_args)
    assert "delete from tile_entities where id=%s" in sql
    assert cursor.execute.call_args[0][1] == (4,)


def test_update_tile_entity_payload_uses_jsonb_cast():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    db.update_tile_entity(7, payload={"ticks_seen": 2}, status="active")
    sql = _sql(cursor.execute.call_args)
    assert "update tile_entities set" in sql
    assert "payload=%s::jsonb" in sql
    args = cursor.execute.call_args[0][1]
    assert json.loads(args[0]) == {"ticks_seen": 2}
    assert args[-1] == 7


def test_entity_kind_contracts_empty_registry_valid():
    assert ENTITY_KIND_CONTRACTS == {}
    assert TICK_RESOLVE_HANDLERS == {}
    assert validate_entity_kind_contracts() == []


def test_registered_kind_without_handler_fails_validation():
    ENTITY_KIND_CONTRACTS["ghost"] = EntityKindContract(
        key="ghost",
        has_tick_handler=True,
        map_mark="?",
    )
    try:
        errors = validate_entity_kind_contracts()
        assert any("tick handler" in e for e in errors)
    finally:
        ENTITY_KIND_CONTRACTS.pop("ghost", None)


def test_zero_rows_fingerprint_matches_pre_entity_payload():
    tiles = [
        TileView(
            x=0, y=0, tile_type="field", owner_fief_id=1, building=None, building_level=0
        ),
        TileView(
            x=1,
            y=0,
            tile_type="forest",
            owner_fief_id=None,
            building=None,
            building_level=0,
        ),
    ]
    common = dict(
        realm_id=1,
        width=2,
        height=1,
        tiles=tiles,
        highlight_fief_id=1,
        claimable=set(),
    )
    baseline = map_fingerprint(**common)
    assert map_fingerprint(**common, entity_rows=None) == baseline
    assert map_fingerprint(**common, entity_rows=[]) == baseline
    assert entity_fingerprint_rows([]) == []
    assert entity_map_marks([]) == []


def test_entity_rows_change_fingerprint_and_png_mark():
    with _fake_entity_kind(map_mark="!"):
        tiles = [
            TileView(
                x=0,
                y=0,
                tile_type="field",
                owner_fief_id=None,
                building=None,
                building_level=0,
            )
        ]
        common = dict(
            realm_id=1,
            width=1,
            height=1,
            tiles=tiles,
            highlight_fief_id=None,
            claimable=set(),
        )
        empty_fp = map_fingerprint(**common)
        rows = entity_fingerprint_rows(
            [
                {
                    "id": 1,
                    "kind": "_test_camp",
                    "x": 0,
                    "y": 0,
                    "payload": {"farm_mult": 0.5},
                    "expires_tick": 10,
                }
            ]
        )
        assert map_fingerprint(**common, entity_rows=rows) != empty_fp
        marks = entity_map_marks(
            [{"id": 1, "kind": "_test_camp", "x": 0, "y": 0, "payload": {}}]
        )
        assert marks == [(0, 0, "!")]
        png_empty = render_map_image(1, 1, tiles)
        png_marked = render_map_image(1, 1, tiles, entity_marks=marks)
        assert png_empty != png_marked


def test_resolve_expires_by_expires_tick_and_dispatches_handler():
    with _fake_entity_kind() as kind:
        updated: list[tuple] = []
        expired: list[int] = []
        rows = [
            {
                "id": 1,
                "kind": kind,
                "x": 0,
                "y": 0,
                "payload": {"farm_mult": 0.8, "ticks_seen": 0},
                "expires_tick": 5,
                "status": "active",
                "created_tick": 1,
            },
            {
                "id": 2,
                "kind": kind,
                "x": 1,
                "y": 0,
                "payload": {"farm_mult": 0.8},
                "expires_tick": 3,
                "status": "active",
                "created_tick": 1,
            },
        ]

        def list_active():
            return [r for r in rows if r["status"] == "active"]

        def expire(eid: int):
            expired.append(eid)
            for r in rows:
                if int(r["id"]) == eid:
                    r["status"] = "expired"
                    return dict(r)
            return None

        def update(eid: int, **fields):
            updated.append((eid, fields))
            for r in rows:
                if int(r["id"]) == eid:
                    r.update(fields)

        lines = resolve_realm_tile_entities(
            TileEntityResolveCtx(
                tick_index=3,
                list_active=list_active,
                expire_entity=expire,
                update_entity=update,
            )
        )
        assert expired == [2]
        assert any("test-camp@0,0:t1" in line for line in lines)
        assert updated and updated[0][0] == 1
        assert updated[0][1]["payload"]["ticks_seen"] == 1


def test_entity_modifier_collected_and_engine_path_reads_it():
    with _fake_entity_kind() as kind:
        ref = active_tile_entity_ref(
            {
                "id": 1,
                "kind": kind,
                "x": 2,
                "y": 3,
                "payload": {"farm_mult": 0.5},
                "expires_tick": 20,
                "created_tick": 10,
            }
        )
        mods = collect_active_modifiers(
            RealmModifierCtx(active_tile_entities=(ref,), tick_index=15)
        )
        assert mods.farm_mult() == pytest.approx(0.5)
        assert mods.modifiers[0].ticks_remaining == 5

        db = MagicMock()
        db.list_active_tile_entities.return_value = [
            {
                "id": 1,
                "kind": kind,
                "x": 2,
                "y": 3,
                "payload": {"farm_mult": 0.5},
                "expires_tick": 20,
                "created_tick": 10,
                "status": "active",
            }
        ]
        db.get_active_events.return_value = []
        engine = Engine(db)
        realm = {"id": 9, "active_minor_key": None, "tick_index": 15}
        assert engine.realm_modifiers(realm).farm_mult() == pytest.approx(0.5)


def test_zero_rows_tick_digest_unchanged_and_single_list_call():
    db = MagicMock()
    db.list_active_tile_entities.return_value = []
    db.list_expired_open_trades.return_value = []
    db.list_fiefs.return_value = []
    db.list_open_trades.return_value = []
    db.get_realm.return_value = {
        "id": 1,
        "title": "Долина",
        "day_number": 3,
        "tick_index": 7,
        "timezone": "Europe/Moscow",
        "active_minor_key": None,
        "pending_raid_lines": [],
        "chat_id": -100,
        "width": 4,
        "height": 4,
    }
    engine = Engine(db)
    engine.apply_absence = MagicMock()  # type: ignore[method-assign]
    engine._prepare_tick_minor = MagicMock(return_value=None)  # type: ignore[method-assign]
    engine._feud_lines = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.maybe_grow_map = MagicMock(return_value=None)  # type: ignore[method-assign]
    engine._sunday_extra = MagicMock(return_value=None)  # type: ignore[method-assign]

    result = engine.run_realm_tick(1, advance_clock=False)
    digest_without = result["digest"]

    db.list_active_tile_entities.reset_mock()
    result2 = engine.run_realm_tick(1, advance_clock=False)
    assert result2["digest"] == digest_without
    # Один SELECT на resolve; realm_modifiers/farm не зовётся без усадеб.
    assert db.list_active_tile_entities.call_count == 1
    db.claim_expire_tile_entity.assert_not_called()


def test_empty_tile_entities_modifier_set_identical():
    base = collect_active_modifiers(RealmModifierCtx(active_minor_key="drought"))
    with_empty = collect_active_modifiers(
        RealmModifierCtx(active_minor_key="drought", active_tile_entities=())
    )
    assert base.modifiers == with_empty.modifiers
    assert base.farm_mult() == with_empty.farm_mult()


def test_fake_kind_end_to_end_tick_via_engine():
    with _fake_entity_kind() as kind:
        entity = {
            "id": 5,
            "kind": kind,
            "x": 1,
            "y": 1,
            "payload": {"farm_mult": 0.75, "ticks_seen": 0},
            "expires_tick": 100,
            "created_tick": 1,
            "status": "active",
        }
        store = [entity]

        db = MagicMock()
        db.list_active_tile_entities.side_effect = lambda rid: [
            dict(e) for e in store if e["status"] == "active"
        ]
        db.list_expired_open_trades.return_value = []
        db.list_fiefs.return_value = []
        db.list_open_trades.return_value = []
        db.get_active_events.return_value = []

        def expire(eid):
            for e in store:
                if int(e["id"]) == int(eid) and e["status"] == "active":
                    e["status"] = "expired"
                    return dict(e)
            return None

        def update(eid, **fields):
            for e in store:
                if int(e["id"]) == int(eid):
                    e.update(fields)

        db.claim_expire_tile_entity.side_effect = expire
        db.update_tile_entity.side_effect = update
        db.get_realm.return_value = {
            "id": 1,
            "title": "Долина",
            "day_number": 1,
            "tick_index": 4,
            "timezone": "Europe/Moscow",
            "active_minor_key": None,
            "pending_raid_lines": [],
            "chat_id": -100,
            "width": 4,
            "height": 4,
        }
        engine = Engine(db)
        engine.apply_absence = MagicMock()  # type: ignore[method-assign]
        engine._prepare_tick_minor = MagicMock(return_value=None)  # type: ignore[method-assign]
        engine._feud_lines = MagicMock(return_value=[])  # type: ignore[method-assign]
        engine.maybe_grow_map = MagicMock(return_value=None)  # type: ignore[method-assign]
        engine._sunday_extra = MagicMock(return_value=None)  # type: ignore[method-assign]

        result = engine.run_realm_tick(1, advance_clock=False)
        assert "test-camp@1,1:t1" in result["digest"]
        assert store[0]["payload"]["ticks_seen"] == 1
        assert engine.realm_modifiers(db.get_realm.return_value).farm_mult() == pytest.approx(
            0.75
        )
