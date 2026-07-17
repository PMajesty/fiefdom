"""Phase 4: tick pipeline capabilities, calendar substrate, action_intents DDL/CRUD."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.database import Database
from app.domain.calendar import (
    game_day_from_tick,
    season_fields,
    season_from_world,
)
from app.domain.tick_pipeline import (
    TICK_PHASE_ECONOMY,
    TICK_PHASE_ORDERS,
    TICK_PHASE_PLAY,
    TICK_PHASE_RESOLVE,
    ActionWindow,
    TickPipeline,
    needs_economy_wake,
    normalize_tick_phase,
    phase_capabilities,
)


def _sql(cursor_execute_call) -> str:
    return " ".join(cursor_execute_call[0][0].split()).lower()


def _db_with_mock_conn() -> tuple[Database, MagicMock]:
    db = Database(connect=False)
    conn = MagicMock()
    db.connection = conn
    db.cursor = MagicMock()
    return db, conn


def test_normalize_keeps_economy_and_maps_unknown_to_play():
    assert normalize_tick_phase("economy") == TICK_PHASE_ECONOMY
    assert normalize_tick_phase("play") == TICK_PHASE_PLAY
    assert normalize_tick_phase(None) == TICK_PHASE_PLAY
    assert normalize_tick_phase("unknown") == TICK_PHASE_PLAY
    assert normalize_tick_phase(TICK_PHASE_ORDERS) == TICK_PHASE_ORDERS
    assert normalize_tick_phase(TICK_PHASE_RESOLVE) == TICK_PHASE_RESOLVE


def test_action_window_live_semantics_unchanged():
    assert ActionWindow.allows(tick_phase="play", incomplete=False) is True
    assert ActionWindow.allows(tick_phase="economy", incomplete=False) is False
    assert ActionWindow.allows(tick_phase="play", incomplete=True) is False
    assert ActionWindow.allows(tick_phase=None, incomplete=False) is True
    # Будущие фазы не открывают мгновенные мутации.
    assert ActionWindow.allows(tick_phase=TICK_PHASE_ORDERS, incomplete=False) is False
    assert ActionWindow.allows_orders(tick_phase=TICK_PHASE_ORDERS, incomplete=False) is True
    assert ActionWindow.allows_orders(tick_phase="play", incomplete=False) is False


def test_phase_capabilities_drive_window():
    assert phase_capabilities("play").allow_mutations is True
    assert phase_capabilities("economy").allow_economy is True
    assert phase_capabilities("economy").allow_mutations is False
    assert phase_capabilities(TICK_PHASE_RESOLVE).allow_mutations is False


def test_tick_pipeline_live_fields_and_sequence():
    assert TickPipeline.economy_fields() == {"tick_phase": "economy"}
    assert TickPipeline.play_fields() == {"tick_phase": "play"}
    assert TickPipeline.next_live_phase("economy") == "play"
    assert TickPipeline.next_live_phase("play") is None
    assert TickPipeline.LIVE_SEQUENCE == ("economy", "play")
    assert "orders" in TickPipeline.TARGET_SEQUENCE
    assert "resolve" in TickPipeline.TARGET_SEQUENCE


def test_needs_economy_wake_unchanged():
    assert needs_economy_wake({"tick_phase": "economy"}) is True
    assert needs_economy_wake({"tick_phase": "play"}) is False
    assert needs_economy_wake({}) is False
    assert needs_economy_wake({"tick_phase": "unknown"}) is False
    assert needs_economy_wake({"tick_phase": TICK_PHASE_ORDERS}) is False


def test_season_from_world_null_is_noop():
    assert season_from_world(None) is None
    assert season_from_world({}) is None
    assert season_from_world({"season_key": None}) is None
    assert season_from_world({"season_key": ""}) is None


def test_season_state_and_fields():
    state = season_from_world(
        {
            "season_key": "winter",
            "season_tick_start": 10,
            "season_length_ticks": 40,
        }
    )
    assert state is not None
    assert state.key == "winter"
    assert state.ticks_elapsed(15) == 5
    assert state.ticks_remaining(15) == 35
    assert season_fields(key=None) == {
        "season_key": None,
        "season_tick_start": None,
        "season_length_ticks": None,
    }
    assert season_fields(key="spring", tick_start=0, length_ticks=20)["season_key"] == (
        "spring"
    )
    assert game_day_from_tick(7) == 7


def test_action_intents_and_season_ddl_present_in_schema_source():
    import inspect

    from app import database as database_mod

    src = inspect.getsource(database_mod.Database._ensure_world_schema)
    assert "CREATE TABLE IF NOT EXISTS action_intents" in src
    assert "season_key" in src
    assert "season_tick_start" in src
    assert "season_length_ticks" in src
    assert "idx_action_intents_world_tick" in src


def test_create_and_list_action_intent_sql():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (
        1,
        2,
        5,
        9,
        "raid",
        '{"target": 3}',
        "open",
        None,
    )
    cursor.description = [
        ("id",),
        ("world_id",),
        ("tick_index",),
        ("fief_id",),
        ("kind",),
        ("payload",),
        ("status",),
        ("created_at",),
    ]
    row = db.create_action_intent(
        world_id=2,
        tick_index=5,
        fief_id=9,
        kind="raid",
        payload={"target": 3},
    )
    assert row["id"] == 1
    assert row["payload"] == {"target": 3}
    sql = _sql(cursor.execute.call_args)
    assert "insert into action_intents" in sql
    assert "%s::jsonb" in sql

    cursor.fetchall.return_value = []
    cursor.description = [("id",)]
    db.list_open_action_intents(2, 5)
    sql = _sql(cursor.execute.call_args)
    assert "status='open'" in sql
    assert cursor.execute.call_args[0][1] == (2, 5)


def test_claim_resolve_action_intent_cas():
    db, _conn = _db_with_mock_conn()
    cursor = db.cursor
    cursor.fetchone.return_value = (4, "resolved")
    cursor.description = [("id",), ("status",)]
    row = db.claim_resolve_action_intent(4)
    assert row == {"id": 4, "status": "resolved"}
    sql = _sql(cursor.execute.call_args)
    assert "status='resolved'" in sql
    assert "status='open'" in sql
    cursor.fetchone.return_value = None
    assert db.claim_resolve_action_intent(4) is None
