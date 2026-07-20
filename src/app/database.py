"""PostgreSQL: схема и доступ к данным Вотчины."""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping
from typing import Any, Iterator

import pg8000

from app.config import DB_CONFIG, tick_slots
from app.domain.resource_bags import (
    normalize_credit_amounts,
    normalize_debit_amounts,
)
from app.domain.resource_registry import live_resource_keys, raid_lootable_defs

from app.resource_schema import (
    build_annul_open_trades_sql,
    build_credit_sql,
    build_debit_sql,
    ensure_resource_columns_sql,
    fief_stash_ddl_lines,
    raids_stolen_ddl_lines,
)
from app.domain.tick_schedule import LEGACY_TWO_TICK_SLOTS, remap_last_tick_slot
from app.repos import (
    ActionIntentRepo,
    DecreeRepo,
    EventRepo,
    FiefRepo,
    PactRepo,
    PatchAnnounceRepo,
    RaidLogRepo,
    RealmRepo,
    TileEntityRepo,
    TradeRepo,
    UnitOfWork,
    UserRepo,
    WorldRepo,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database(
    WorldRepo,
    RealmRepo,
    UserRepo,
    FiefRepo,
    PactRepo,
    TradeRepo,
    RaidLogRepo,
    EventRepo,
    TileEntityRepo,
    ActionIntentRepo,
    PatchAnnounceRepo,
    DecreeRepo,
    UnitOfWork,
):
    def __init__(self, connect: bool = True):
        self.lock = threading.RLock()
        self.connection = None
        self.cursor = None
        self._tx_depth = 0
        if connect:
            self.connect()

    def connect(self) -> None:
        self.connection = pg8000.connect(
            database=DB_CONFIG["NAME"],
            user=DB_CONFIG["USER"],
            password=DB_CONFIG["PASSWORD"],
            host=DB_CONFIG["HOST"],
            port=int(DB_CONFIG["PORT"]),
        )
        self.cursor = self.connection.cursor()
        self.create_tables()
        self.connection.commit()

    def close(self) -> None:
        with self.lock:
            if self.connection:
                self.connection.close()
                self.connection = None
                self.cursor = None

    def commit(self) -> None:
        # Внутри transaction() коммит откладывается до выхода из блока.
        if self._tx_depth > 0:
            return
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def _rollback_outside_tx(self) -> None:
        """Сброс aborted-транзакции, чтобы один сбой не травил всё соединение."""
        if self._tx_depth > 0 or self.connection is None:
            return
        try:
            self.connection.rollback()
        except Exception:
            logger.exception("db rollback after error failed")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Атомарный блок: промежуточные commit() no-op, в конце COMMIT или ROLLBACK."""
        with self.lock:
            nested = self._tx_depth > 0
            self._tx_depth += 1
            try:
                yield
                if not nested:
                    self.connection.commit()
            except Exception:
                if not nested:
                    self.connection.rollback()
                raise
            finally:
                self._tx_depth -= 1

    def create_tables(self) -> None:
        with self.lock:
            stmts = [
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id BIGINT PRIMARY KEY,
                    username TEXT,
                    display_name TEXT,
                    last_realm_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS realms (
                    id BIGSERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL UNIQUE,
                    title TEXT NOT NULL DEFAULT 'Долина',
                    day_number INT NOT NULL DEFAULT 1,
                    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    tick_hour INT NOT NULL DEFAULT 13,
                    tick_minute INT NOT NULL DEFAULT 0,
                    width INT NOT NULL,
                    height INT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_tick_at TIMESTAMPTZ,
                    last_tick_local_date DATE,
                    next_catastrophe_at TIMESTAMPTZ,
                    next_catastrophe_tick INT,
                    next_catastrophe_key TEXT,
                    last_catastrophe_key TEXT,
                    active_minor_key TEXT,
                    active_minor_until TIMESTAMPTZ,
                    pending_minor_key TEXT,
                    tick_index INT NOT NULL DEFAULT 0,
                    balance_overrides JSONB NOT NULL DEFAULT '{}',
                    feature_flags JSONB NOT NULL DEFAULT '{}',
                    pending_raid_lines JSONB NOT NULL DEFAULT '[]',
                    last_digest_text TEXT,
                    last_rumor_lines JSONB NOT NULL DEFAULT '[]',
                    rumor_queue JSONB NOT NULL DEFAULT '[]',
                    wipe_confirm_code TEXT,
                    wipe_confirm_until TIMESTAMPTZ,
                    forced_tick_count INT NOT NULL DEFAULT 0,
                    clock_mode TEXT NOT NULL DEFAULT 'shared'
                );
                """,
                f"""
                CREATE TABLE IF NOT EXISTS fiefs (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(telegram_id),
                    world_id BIGINT,
                    name TEXT NOT NULL,
                    {", ".join(fief_stash_ddl_lines())},
                    actions INT NOT NULL DEFAULT 1,
                    hungry BOOLEAN NOT NULL DEFAULT FALSE,
                    patrol_until TIMESTAMPTZ,
                    shield_until TIMESTAMPTZ,
                    patrol_until_tick INT,
                    shield_until_tick INT,
                    last_raid_at TIMESTAMPTZ,
                    last_raid_tick INT,
                    onboard_step INT NOT NULL DEFAULT 1,
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_active_tick INT,
                    pact_id BIGINT,
                    cover_allies BOOLEAN NOT NULL DEFAULT FALSE,
                    pact_left_tick INT,
                    frozen BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE (realm_id, user_id)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS map_tiles (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    x INT NOT NULL,
                    y INT NOT NULL,
                    tile_type TEXT NOT NULL,
                    is_bridge BOOLEAN NOT NULL DEFAULT FALSE,
                    owner_fief_id BIGINT REFERENCES fiefs(id) ON DELETE SET NULL,
                    building TEXT,
                    building_level INT NOT NULL DEFAULT 0,
                    damaged BOOLEAN NOT NULL DEFAULT FALSE,
                    is_core BOOLEAN NOT NULL DEFAULT FALSE,
                    is_overgrown BOOLEAN NOT NULL DEFAULT FALSE,
                    ruins_looted BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE (realm_id, x, y)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS pacts (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    founder_fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS pact_invites (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    pact_id BIGINT NOT NULL REFERENCES pacts(id) ON DELETE CASCADE,
                    inviter_fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    target_fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    expires_tick INT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS trade_offers (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    offerer_fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    target_fief_id BIGINT REFERENCES fiefs(id) ON DELETE CASCADE,
                    give_res TEXT NOT NULL,
                    give_amt INT NOT NULL,
                    want_res TEXT NOT NULL,
                    want_amt INT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    expires_tick INT
                );
                """,
                f"""
                CREATE TABLE IF NOT EXISTS raids_log (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    attacker_fief_id BIGINT NOT NULL,
                    victim_fief_id BIGINT NOT NULL,
                    success BOOLEAN NOT NULL,
                    might_spent INT NOT NULL,
                    {", ".join(raids_stolen_ddl_lines())},
                    public_line TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    tick_index INT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS realm_events (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}',
                    narrative TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    resolves_at TIMESTAMPTZ,
                    resolves_tick INT
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS event_actions (
                    id BIGSERIAL PRIMARY KEY,
                    event_id BIGINT NOT NULL REFERENCES realm_events(id) ON DELETE CASCADE,
                    fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    action_key TEXT NOT NULL DEFAULT 'default',
                    amount INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (event_id, fief_id, action_key)
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS decrees (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT REFERENCES realms(id) ON DELETE CASCADE,
                    number INT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS announced_patches (
                    name TEXT PRIMARY KEY,
                    announced_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS personal_deals (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL,
                    fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    give_res TEXT NOT NULL,
                    give_amt INT NOT NULL,
                    want_res TEXT NOT NULL,
                    want_amt INT NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    expires_tick INT,
                    status TEXT NOT NULL DEFAULT 'open'
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS tile_entities (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    x INT NOT NULL,
                    y INT NOT NULL,
                    kind TEXT NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_tick INT NOT NULL,
                    expires_tick INT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_fiefs_realm ON fiefs(realm_id);
                CREATE INDEX IF NOT EXISTS idx_tiles_realm ON map_tiles(realm_id);
                CREATE INDEX IF NOT EXISTS idx_tiles_owner ON map_tiles(owner_fief_id);
                CREATE INDEX IF NOT EXISTS idx_raids_realm_time ON raids_log(realm_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_trade_realm ON trade_offers(realm_id, status);
                CREATE INDEX IF NOT EXISTS idx_pact_invites_target
                    ON pact_invites(target_fief_id, status);
                CREATE INDEX IF NOT EXISTS idx_tile_entities_realm
                    ON tile_entities(realm_id);
                CREATE INDEX IF NOT EXISTS idx_tile_entities_realm_xy
                    ON tile_entities(realm_id, x, y);
                """,
            ]
            for s in stmts:
                self.cursor.execute(s)
            self.cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_pact_invites_open_unique
                ON pact_invites (pact_id, target_fief_id)
                WHERE status = 'open';
                """
            )
            # FK pact_id after pacts exists
            self.cursor.execute(
                """
                DO $$ BEGIN
                    ALTER TABLE fiefs
                    ADD CONSTRAINT fiefs_pact_fk
                    FOREIGN KEY (pact_id) REFERENCES pacts(id) ON DELETE SET NULL;
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
            self.cursor.execute(
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS last_digest_text TEXT;"
            )
            self.cursor.execute(
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS last_rumor_lines "
                "JSONB NOT NULL DEFAULT '[]';"
            )
            self.cursor.execute(
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS rumor_queue "
                "JSONB NOT NULL DEFAULT '[]';"
            )
            self.cursor.execute(
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS last_tick_slot INT;"
            )
            # NULL = ни один слот расписания ещё не закрыт (0 = утренний слот уже прошёл).
            self.cursor.execute(
                "ALTER TABLE realms ALTER COLUMN last_tick_slot DROP NOT NULL;"
            )
            self.cursor.execute(
                "ALTER TABLE realms ALTER COLUMN last_tick_slot DROP DEFAULT;"
            )
            self.cursor.execute(
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS forced_tick_count "
                "INT NOT NULL DEFAULT 0;"
            )
            for stmt in (
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS tick_index INT NOT NULL DEFAULT 0;",
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS next_catastrophe_tick INT;",
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS next_catastrophe_key TEXT;",
                "ALTER TABLE realms ADD COLUMN IF NOT EXISTS pending_minor_key TEXT;",
                "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS patrol_until_tick INT;",
                "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS shield_until_tick INT;",
                "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS last_raid_tick INT;",
                "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS last_active_tick INT;",
                "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS pact_left_tick INT;",
                "ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE pact_invites ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE personal_deals ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE realm_events ADD COLUMN IF NOT EXISTS resolves_tick INT;",
                "ALTER TABLE raids_log ADD COLUMN IF NOT EXISTS tick_index INT;",
                *ensure_resource_columns_sql(),
            ):
                self.cursor.execute(stmt)
            # Застава: без живой стойки авто-перехват выключен (legacy DEFAULT TRUE).
            self.cursor.execute(
                "ALTER TABLE fiefs ALTER COLUMN cover_allies SET DEFAULT FALSE;"
            )
            self.cursor.execute(
                """
                UPDATE fiefs AS f
                SET cover_allies = FALSE
                WHERE f.cover_allies = TRUE
                  AND NOT EXISTS (
                    SELECT 1 FROM action_intents ai
                    WHERE ai.fief_id = f.id
                      AND ai.kind = 'cover_stance'
                      AND ai.status IN ('open', 'locked')
                  );
                """
            )
            # Старые wall-clock сроки без тика - считаем уже истёкшими.
            self.cursor.execute(
                "UPDATE trade_offers SET expires_tick = 0 "
                "WHERE expires_tick IS NULL AND status = 'open';"
            )
            self.cursor.execute(
                "UPDATE pact_invites SET expires_tick = 0 "
                "WHERE expires_tick IS NULL AND status = 'open';"
            )
            self.cursor.execute(
                "UPDATE personal_deals SET expires_tick = 0 "
                "WHERE expires_tick IS NULL AND status = 'open';"
            )
            self.cursor.execute(
                """
                UPDATE realms
                SET next_catastrophe_tick = tick_index + 10
                WHERE next_catastrophe_tick IS NULL
                  AND next_catastrophe_at IS NOT NULL;
                """
            )
            # Сброс устаревших wall-clock ворот миноров/щитов/дозора.
            self.cursor.execute(
                "UPDATE realms SET active_minor_until = NULL "
                "WHERE active_minor_until IS NOT NULL;"
            )
            self.cursor.execute(
                "UPDATE fiefs SET patrol_until = NULL, shield_until = NULL "
                "WHERE patrol_until IS NOT NULL OR shield_until IS NOT NULL;"
            )
            self._ensure_world_schema()
            self._apply_patch_annul_open_trades()
            self._apply_patch_remap_tick_slots_2_to_4()

    def _apply_patch_annul_open_trades(self) -> None:
        """Разовые возвраты эскроу при смене правил рынка."""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_patches (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        # v1: запрет снятия лота; v2: отказ от эскроу (ресурс остаётся на усадьбе).
        for patch_name in (
            "annul_open_trades_no_cancel_v1",
            "annul_open_trades_no_escrow_v2",
        ):
            self._run_annul_open_trades_patch(patch_name)

    def _run_annul_open_trades_patch(self, patch_name: str) -> None:
        self.cursor.execute(
            "SELECT 1 FROM applied_patches WHERE name=%s;",
            (patch_name,),
        )
        if self.cursor.fetchone() is not None:
            return
        # Точный возврат give_amt без бонусов и без обрезки склада.
        # Суммы по усадьбе одним UPDATE: иначе PG молча теряет
        # повторные правки одной строки fiefs в одном statement.
        self.cursor.execute(build_annul_open_trades_sql())
        self.cursor.execute(
            "INSERT INTO applied_patches (name) VALUES (%s);",
            (patch_name,),
        )

    def _apply_patch_remap_tick_slots_2_to_4(self) -> None:
        """Индексы last_tick_slot: старые 0/1 (13:00/19:00) -> 1/3 при 4 слотах."""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS applied_patches (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        patch_name = "remap_tick_slots_2_to_4_v1"
        self.cursor.execute(
            "SELECT 1 FROM applied_patches WHERE name=%s;",
            (patch_name,),
        )
        if self.cursor.fetchone() is not None:
            return
        try:
            slots = tick_slots()
        except ValueError:
            logger.warning(
                "skip %s: текущие tick_slots невалидны (поправьте .env)",
                patch_name,
            )
            return
        if slots != [(10, 0), (13, 0), (16, 0), (19, 0)]:
            logger.info(
                "skip %s: tick_slots=%s не целевой 4-слотовый layout",
                patch_name,
                slots,
            )
            self.cursor.execute(
                "INSERT INTO applied_patches (name) VALUES (%s);",
                (patch_name,),
            )
            return

        self.cursor.execute(
            "SELECT id, last_tick_slot FROM worlds WHERE last_tick_slot IS NOT NULL;"
        )
        for world_id, last_slot in self.cursor.fetchall() or []:
            new_slot = remap_last_tick_slot(
                last_slot,
                from_slots=LEGACY_TWO_TICK_SLOTS,
                to_slots=slots,
            )
            if new_slot == last_slot:
                continue
            self.cursor.execute(
                "UPDATE worlds SET last_tick_slot=%s WHERE id=%s;",
                (new_slot, int(world_id)),
            )
        self.cursor.execute(
            "SELECT id, last_tick_slot FROM realms WHERE last_tick_slot IS NOT NULL;"
        )
        for realm_id, last_slot in self.cursor.fetchall() or []:
            new_slot = remap_last_tick_slot(
                last_slot,
                from_slots=LEGACY_TWO_TICK_SLOTS,
                to_slots=slots,
            )
            if new_slot == last_slot:
                continue
            self.cursor.execute(
                "UPDATE realms SET last_tick_slot=%s WHERE id=%s;",
                (new_slot, int(realm_id)),
            )
        self.cursor.execute(
            "INSERT INTO applied_patches (name) VALUES (%s);",
            (patch_name,),
        )
        logger.info("applied patch %s", patch_name)

    def _ensure_world_schema(self) -> None:
        """Континент: один world, линейный chain_index, зеркало часов на realms."""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS worlds (
                id BIGSERIAL PRIMARY KEY,
                name TEXT NOT NULL DEFAULT 'Континент',
                day_number INT NOT NULL DEFAULT 1,
                tick_index INT NOT NULL DEFAULT 0,
                timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
                last_tick_at TIMESTAMPTZ,
                last_tick_local_date DATE,
                last_tick_slot INT,
                next_catastrophe_tick INT,
                next_catastrophe_key TEXT,
                last_catastrophe_key TEXT,
                next_catastrophe_at TIMESTAMPTZ,
                active_minor_key TEXT,
                active_minor_until TIMESTAMPTZ,
                pending_minor_key TEXT,
                tick_phase TEXT NOT NULL DEFAULT 'play',
                forced_tick_count INT NOT NULL DEFAULT 0,
                wipe_confirm_code TEXT,
                wipe_confirm_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS tick_phase "
            "TEXT NOT NULL DEFAULT 'play';"
        )
        # Сезон - опциональный substrate; NULL = нет сезона (live no-op).
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS season_key TEXT;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS season_tick_start INT;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS season_length_ticks INT;"
        )
        # Идентичность миров: continent (live) / instance (будущие temp realms).
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS world_kind "
            "TEXT NOT NULL DEFAULT 'continent';"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS parent_world_id BIGINT "
            "REFERENCES worlds(id);"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS expires_tick INT;"
        )
        # Окно play для half-tick lock заявок набега; resolve_tick_index - bookmark crash.
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS play_opened_at TIMESTAMPTZ;"
        )
        # Какой play_opened_at уже получил rumor_queue (не путать с drained []).
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS "
            "rumor_plan_play_opened_at TIMESTAMPTZ;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS resolve_tick_index INT;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS early_tick_at TIMESTAMPTZ;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS "
            "declare_midpoint_at TIMESTAMPTZ;"
        )
        self.cursor.execute(
            "ALTER TABLE worlds ADD COLUMN IF NOT EXISTS "
            "early_tick_pending_slot INT;"
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS early_tick_votes (
                world_id BIGINT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (world_id, user_id)
            );
            """
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS realm_kind "
            "TEXT NOT NULL DEFAULT 'valley';"
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS expires_tick INT;"
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS action_intents (
                id BIGSERIAL PRIMARY KEY,
                world_id BIGINT NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
                tick_index INT NOT NULL,
                fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_intents_world_tick
            ON action_intents(world_id, tick_index, status);
            """
        )
        self.cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_action_intents_fief
            ON action_intents(fief_id, status);
            """
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS world_id BIGINT "
            "REFERENCES worlds(id);"
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS chain_index INT;"
        )
        self.cursor.execute("DROP TABLE IF EXISTS tick_force_votes;")
        self.cursor.execute(
            "ALTER TABLE raids_log ADD COLUMN IF NOT EXISTS victim_realm_id BIGINT;"
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS last_economy_tick INT;"
        )
        self.cursor.execute(
            """
            UPDATE realms
            SET last_economy_tick = tick_index
            WHERE last_economy_tick IS NULL;
            """
        )
        # Один континент + миграция существующих долин.
        self.cursor.execute("SELECT id FROM worlds ORDER BY id LIMIT 1;")
        world_row = self.cursor.fetchone()
        if not world_row:
            self.cursor.execute(
                """
                INSERT INTO worlds (name, day_number, tick_index, timezone)
                VALUES ('Континент', 1, 0, 'Europe/Moscow')
                RETURNING id;
                """
            )
            world_id = int(self.cursor.fetchone()[0])
        else:
            world_id = int(world_row[0])

        self.cursor.execute(
            "SELECT COUNT(*) FROM realms WHERE world_id IS NULL OR chain_index IS NULL;"
        )
        need_attach = int(self.cursor.fetchone()[0] or 0)
        if need_attach > 0:
            self.cursor.execute(
                """
                SELECT id, tick_index, day_number, timezone,
                       last_tick_at, last_tick_local_date, last_tick_slot,
                       next_catastrophe_tick, next_catastrophe_key,
                       last_catastrophe_key, active_minor_key, pending_minor_key,
                       forced_tick_count
                FROM realms
                ORDER BY tick_index DESC, day_number DESC, id ASC
                LIMIT 1;
                """
            )
            leader = self.cursor.fetchone()
            if leader:
                (
                    _lid,
                    l_tick,
                    l_day,
                    l_tz,
                    l_tick_at,
                    l_tick_date,
                    l_tick_slot,
                    l_next_cat_tick,
                    l_next_cat_key,
                    l_last_cat_key,
                    l_active_minor,
                    l_pending_minor,
                    l_forced,
                ) = leader
                self.cursor.execute(
                    """
                    UPDATE worlds SET
                        day_number=%s, tick_index=%s, timezone=%s,
                        last_tick_at=%s, last_tick_local_date=%s, last_tick_slot=%s,
                        next_catastrophe_tick=%s, next_catastrophe_key=%s,
                        last_catastrophe_key=%s, active_minor_key=%s,
                        pending_minor_key=%s, forced_tick_count=%s
                    WHERE id=%s;
                    """,
                    (
                        int(l_day or 1),
                        int(l_tick or 0),
                        l_tz or "Europe/Moscow",
                        l_tick_at,
                        l_tick_date,
                        l_tick_slot,
                        l_next_cat_tick,
                        l_next_cat_key,
                        l_last_cat_key,
                        l_active_minor,
                        l_pending_minor,
                        int(l_forced or 0),
                        world_id,
                    ),
                )
                g_tick = int(l_tick or 0)
                self.cursor.execute("SELECT id, tick_index FROM realms ORDER BY id;")
                for rid, r_tick in self.cursor.fetchall():
                    delta = g_tick - int(r_tick or 0)
                    if delta == 0:
                        continue
                    rid_i = int(rid)
                    for col in (
                        "patrol_until_tick",
                        "shield_until_tick",
                        "last_raid_tick",
                        "last_active_tick",
                    ):
                        self.cursor.execute(
                            f"UPDATE fiefs SET {col} = {col} + %s "
                            f"WHERE realm_id=%s AND {col} IS NOT NULL;",
                            (delta, rid_i),
                        )
                    for table, col in (
                        ("trade_offers", "expires_tick"),
                        ("pact_invites", "expires_tick"),
                        ("personal_deals", "expires_tick"),
                        ("realm_events", "resolves_tick"),
                        ("raids_log", "tick_index"),
                        ("tile_entities", "created_tick"),
                        ("tile_entities", "expires_tick"),
                    ):
                        self.cursor.execute(
                            f"UPDATE {table} SET {col} = {col} + %s "
                            f"WHERE realm_id=%s AND {col} IS NOT NULL;",
                            (delta, rid_i),
                        )

            self.cursor.execute(
                """
                WITH ordered AS (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY id) - 1 AS idx
                    FROM realms
                )
                UPDATE realms r
                SET world_id = %s,
                    chain_index = ordered.idx
                FROM ordered
                WHERE r.id = ordered.id;
                """,
                (world_id,),
            )
            self.cursor.execute(
                """
                UPDATE realms SET
                    day_number = w.day_number,
                    tick_index = w.tick_index,
                    timezone = w.timezone,
                    last_tick_at = w.last_tick_at,
                    last_tick_local_date = w.last_tick_local_date,
                    last_tick_slot = w.last_tick_slot,
                    next_catastrophe_tick = w.next_catastrophe_tick,
                    next_catastrophe_key = w.next_catastrophe_key,
                    last_catastrophe_key = w.last_catastrophe_key,
                    active_minor_key = w.active_minor_key,
                    pending_minor_key = w.pending_minor_key,
                    forced_tick_count = w.forced_tick_count
                FROM worlds w
                WHERE w.id = %s AND realms.world_id = w.id;
                """,
                (world_id,),
            )

        self.cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_realms_world_chain
            ON realms(world_id, chain_index);
            """
        )
        self.cursor.execute(
            """
            UPDATE raids_log
            SET victim_realm_id = realm_id
            WHERE victim_realm_id IS NULL;
            """
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS clock_mode "
            "TEXT NOT NULL DEFAULT 'shared';"
        )
        self.cursor.execute(
            "UPDATE realms SET clock_mode = 'shared' WHERE clock_mode IS NULL;"
        )
        self.cursor.execute(
            "ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS world_id BIGINT;"
        )
        self.cursor.execute(
            """
            UPDATE fiefs AS f
            SET world_id = r.world_id
            FROM realms r
            WHERE f.realm_id = r.id
              AND f.world_id IS NULL
              AND r.world_id IS NOT NULL;
            """
        )
        self.cursor.execute("SELECT COUNT(*) FROM fiefs WHERE world_id IS NULL;")
        null_world = int(self.cursor.fetchone()[0] or 0)
        if null_world > 0:
            raise RuntimeError(
                f"fiefs identity migration blocked: {null_world} fiefs without world_id"
            )
        self.cursor.execute(
            """
            SELECT user_id, world_id, COUNT(*) AS n
            FROM fiefs
            GROUP BY user_id, world_id
            HAVING COUNT(*) > 1
            LIMIT 5;
            """
        )
        dupes = self.cursor.fetchall() or []
        if dupes:
            raise RuntimeError(
                "fiefs identity migration blocked: duplicate (user_id, world_id) "
                f"rows: {dupes}"
            )
        self.cursor.execute("DROP INDEX IF EXISTS idx_fiefs_user_id;")
        self.cursor.execute(
            "ALTER TABLE fiefs DROP CONSTRAINT IF EXISTS fiefs_user_id_key;"
        )
        self.cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fiefs_user_world
            ON fiefs(user_id, world_id);
            """
        )
        self.cursor.execute(
            "ALTER TABLE fiefs ALTER COLUMN world_id SET NOT NULL;"
        )
        self.cursor.execute(
            """
            DO $$ BEGIN
                ALTER TABLE fiefs
                ADD CONSTRAINT fiefs_world_id_fk
                FOREIGN KEY (world_id) REFERENCES worlds(id);
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$;
            """
        )

    # --- world ---
    def get_or_create_world(self) -> dict:
        row = self._fetchone("SELECT * FROM worlds ORDER BY id LIMIT 1;")
        if row:
            return row
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO worlds (name, world_kind)
                VALUES ('Континент', 'continent') RETURNING *;
                """
            )
            cols = [d[0] for d in self.cursor.description]
            data = dict(zip(cols, self.cursor.fetchone()))
            self.commit()
            return data

    def create_instance_world(
        self,
        *,
        name: str,
        parent_world_id: int,
        expires_tick: int | None = None,
        timezone: str = "Europe/Moscow",
    ) -> dict:
        """Substrate: отдельный world для temp-realm (не вызывается live-путём)."""
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO worlds (
                    name, world_kind, parent_world_id, expires_tick, timezone
                ) VALUES (%s, 'instance', %s, %s, %s)
                RETURNING *;
                """,
                (name, int(parent_world_id), expires_tick, timezone),
            )
            cols = [d[0] for d in self.cursor.description]
            data = dict(zip(cols, self.cursor.fetchone()))
            self.commit()
            return data

    def get_world(self, world_id: int | None = None) -> dict | None:
        if world_id is None:
            return self.get_or_create_world()
        return self._fetchone("SELECT * FROM worlds WHERE id=%s;", (int(world_id),))

    def update_world(self, world_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        vals = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            vals.append(v)
        vals.append(int(world_id))
        with self.lock:
            self.cursor.execute(
                f"UPDATE worlds SET {', '.join(cols)} WHERE id=%s;",
                tuple(vals),
            )
            self.commit()

    def sync_realms_clock_from_world(self, world_id: int) -> None:
        """Зеркалит часы мира на долины с clock_mode=shared."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE realms SET
                    day_number = w.day_number,
                    tick_index = w.tick_index,
                    timezone = w.timezone,
                    last_tick_at = w.last_tick_at,
                    last_tick_local_date = w.last_tick_local_date,
                    last_tick_slot = w.last_tick_slot,
                    next_catastrophe_tick = w.next_catastrophe_tick,
                    next_catastrophe_key = w.next_catastrophe_key,
                    last_catastrophe_key = w.last_catastrophe_key,
                    active_minor_key = w.active_minor_key,
                    pending_minor_key = w.pending_minor_key,
                    forced_tick_count = w.forced_tick_count
                FROM worlds w
                WHERE w.id = %s
                  AND realms.world_id = w.id
                  AND realms.clock_mode = 'shared';
                """,
                (int(world_id),),
            )
            self.commit()

    def list_realms_by_chain(self, world_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM realms WHERE world_id=%s ORDER BY chain_index, id;",
            (int(world_id),),
        )

    def shift_chain_indices(self, world_id: int, from_index: int, delta: int = 1) -> None:
        """Сдвигает индексы по одному с хвоста, чтобы не бить UNIQUE(world_id, chain_index)."""
        with self.lock:
            self.cursor.execute(
                """
                SELECT id, chain_index FROM realms
                WHERE world_id=%s AND chain_index >= %s
                ORDER BY chain_index DESC;
                """,
                (int(world_id), int(from_index)),
            )
            rows = self.cursor.fetchall()
            for rid, idx in rows:
                self.cursor.execute(
                    "UPDATE realms SET chain_index=%s WHERE id=%s;",
                    (int(idx) + int(delta), int(rid)),
                )
            self.commit()

    def recompact_chain_indices(self, world_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                """
                WITH ordered AS (
                    SELECT id, ROW_NUMBER() OVER (ORDER BY chain_index, id) - 1 AS idx
                    FROM realms WHERE world_id=%s
                )
                UPDATE realms r
                SET chain_index = ordered.idx
                FROM ordered
                WHERE r.id = ordered.id;
                """,
                (int(world_id),),
            )
            self.commit()

    def list_adjacent_realms(self, realm_id: int) -> list[dict]:
        """Другие долины того же континента."""
        realm = self.get_realm(realm_id)
        if not realm or realm.get("world_id") is None:
            return []
        return self._fetchall(
            """
            SELECT * FROM realms
            WHERE world_id=%s
              AND id <> %s
            ORDER BY chain_index NULLS LAST, id;
            """,
            (int(realm["world_id"]), int(realm_id)),
        )

    def realms_are_adjacent(self, realm_a: int, realm_b: int) -> bool:
        """True если одна долина или обе на одном континенте."""
        if int(realm_a) == int(realm_b):
            return True
        a = self.get_realm(realm_a)
        b = self.get_realm(realm_b)
        if not a or not b:
            return False
        if a.get("world_id") is None or a.get("world_id") != b.get("world_id"):
            return False
        return True

    # --- users ---
    def upsert_user(self, telegram_id: int, username: str | None, display_name: str) -> None:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO users (telegram_id, username, display_name, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (telegram_id) DO UPDATE SET
                    username = EXCLUDED.username,
                    display_name = EXCLUDED.display_name,
                    updated_at = NOW();
                """,
                (telegram_id, username, display_name),
            )
            self.commit()

    def set_last_realm(self, user_id: int, realm_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                "UPDATE users SET last_realm_id=%s, updated_at=NOW() WHERE telegram_id=%s;",
                (realm_id, user_id),
            )
            self.commit()

    def get_user(self, telegram_id: int) -> dict | None:
        with self.lock:
            self.cursor.execute("SELECT * FROM users WHERE telegram_id=%s;", (telegram_id,))
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            return dict(zip(cols, row))

    # --- realms ---
    def get_realm_by_chat(self, chat_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM realms WHERE chat_id=%s;", (chat_id,))

    def get_realm(self, realm_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM realms WHERE id=%s;", (realm_id,))

    def list_realms(self) -> list[dict]:
        return self._fetchall("SELECT * FROM realms ORDER BY id;")

    def create_realm(
        self,
        chat_id: int,
        title: str,
        width: int,
        height: int,
        timezone: str,
        tick_hour: int,
        tick_minute: int,
        feature_flags: dict,
        next_catastrophe_tick: int,
        *,
        world_id: int | None = None,
        chain_index: int | None = None,
        day_number: int = 1,
        tick_index: int = 0,
        last_tick_local_date=None,
        last_tick_slot: int | None = None,
        next_catastrophe_key: str | None = None,
        pending_minor_key: str | None = None,
        active_minor_key: str | None = None,
        clock_mode: str = "shared",
        realm_kind: str = "valley",
        expires_tick: int | None = None,
    ) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO realms (
                    chat_id, title, width, height, timezone, tick_hour, tick_minute,
                    feature_flags, next_catastrophe_tick, tick_index, day_number,
                    world_id, chain_index, last_tick_local_date, last_tick_slot,
                    next_catastrophe_key, pending_minor_key, active_minor_key,
                    clock_mode, realm_kind, expires_tick
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s
                )
                RETURNING *;
                """,
                (
                    chat_id,
                    title,
                    width,
                    height,
                    timezone,
                    tick_hour,
                    tick_minute,
                    json.dumps(feature_flags),
                    next_catastrophe_tick,
                    int(tick_index),
                    int(day_number),
                    world_id,
                    chain_index,
                    last_tick_local_date,
                    last_tick_slot,
                    next_catastrophe_key,
                    pending_minor_key,
                    active_minor_key,
                    clock_mode,
                    realm_kind,
                    expires_tick,
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def update_realm(self, realm_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = []
        vals = []
        for k, v in fields.items():
            if k in (
                "feature_flags",
                "balance_overrides",
                "pending_raid_lines",
                "last_rumor_lines",
                "rumor_queue",
            ) and not isinstance(v, str):
                v = json.dumps(v)
                cols.append(f"{k}=%s::jsonb")
            else:
                cols.append(f"{k}=%s")
            vals.append(v)
        vals.append(realm_id)
        with self.lock:
            self.cursor.execute(
                f"UPDATE realms SET {', '.join(cols)} WHERE id=%s;",
                tuple(vals),
            )
            self.commit()

    def delete_realm(self, realm_id: int) -> None:
        with self.lock:
            self.cursor.execute("DELETE FROM realms WHERE id=%s;", (realm_id,))
            self.commit()

    # --- tiles ---
    def insert_tiles(self, realm_id: int, tiles: list[dict]) -> None:
        with self.lock:
            for t in tiles:
                self.cursor.execute(
                    """
                    INSERT INTO map_tiles (
                        realm_id, x, y, tile_type, is_bridge, ruins_looted
                    ) VALUES (%s,%s,%s,%s,%s,%s);
                    """,
                    (
                        realm_id,
                        t["x"],
                        t["y"],
                        t["tile_type"],
                        t.get("is_bridge", False),
                        t.get("ruins_looted", False),
                    ),
                )
            self.commit()

    def get_tiles(self, realm_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM map_tiles WHERE realm_id=%s ORDER BY y, x;",
            (realm_id,),
        )

    def get_tile(self, realm_id: int, x: int, y: int) -> dict | None:
        return self._fetchone(
            "SELECT * FROM map_tiles WHERE realm_id=%s AND x=%s AND y=%s;",
            (realm_id, x, y),
        )

    def get_tile_by_id(self, tile_id: int, realm_id: int) -> dict | None:
        return self._fetchone(
            "SELECT * FROM map_tiles WHERE id=%s AND realm_id=%s;",
            (int(tile_id), int(realm_id)),
        )

    def update_tile(self, tile_id: int, **fields: Any) -> None:
        self._update("map_tiles", tile_id, fields)

    def claim_unowned_tile(self, tile_id: int, realm_id: int, **fields: Any) -> dict | None:
        """CAS: занять клетку только если owner_fief_id IS NULL. None при гонке."""
        if not fields:
            raise ValueError("claim_unowned_tile: нет полей для UPDATE")
        cols = []
        vals: list[Any] = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            vals.append(v)
        vals.extend([int(tile_id), int(realm_id)])
        with self.lock:
            self.cursor.execute(
                f"""
                UPDATE map_tiles SET {', '.join(cols)}
                WHERE id=%s AND realm_id=%s AND owner_fief_id IS NULL
                RETURNING *;
                """,
                tuple(vals),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols_desc = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols_desc, row)))

    def fief_tiles(self, fief_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM map_tiles WHERE owner_fief_id=%s ORDER BY y, x;",
            (fief_id,),
        )

    # --- fiefs ---
    def create_fief(self, realm_id: int, user_id: int, name: str, **resources: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                "SELECT tick_index, world_id FROM realms WHERE id=%s;",
                (realm_id,),
            )
            row_tick = self.cursor.fetchone()
            if not row_tick or row_tick[1] is None:
                raise ValueError("Долина не привязана к миру")
            tick_index = int(row_tick[0] or 0)
            world_id = int(row_tick[1])
            res_keys = live_resource_keys()
            res_cols = ", ".join(res_keys)
            res_placeholders = ", ".join(["%s"] * len(res_keys))
            res_vals = tuple(int(resources.get(key, 0) or 0) for key in res_keys)
            self.cursor.execute(
                f"""
                INSERT INTO fiefs (
                    realm_id, user_id, world_id, name, {res_cols}, actions,
                    onboard_step, last_active_tick
                ) VALUES (%s,%s,%s,%s,{res_placeholders},%s,%s,%s)
                RETURNING *;
                """,
                (
                    realm_id,
                    user_id,
                    world_id,
                    name,
                    *res_vals,
                    resources.get("actions", 1),
                    resources.get("onboard_step", 1),
                    resources.get("last_active_tick", tick_index),
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def get_fief(self, fief_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM fiefs WHERE id=%s;", (fief_id,))

    def get_fief_by_user(self, realm_id: int, user_id: int) -> dict | None:
        return self._fetchone(
            "SELECT * FROM fiefs WHERE realm_id=%s AND user_id=%s;",
            (realm_id, user_id),
        )

    def get_fief_by_user_world(self, user_id: int, world_id: int) -> dict | None:
        return self._fetchone(
            "SELECT * FROM fiefs WHERE user_id=%s AND world_id=%s;",
            (int(user_id), int(world_id)),
        )

    def list_fiefs(self, realm_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM fiefs WHERE realm_id=%s ORDER BY id;",
            (realm_id,),
        )

    def list_fiefs_by_user(self, user_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM fiefs WHERE user_id=%s ORDER BY id;",
            (user_id,),
        )

    def list_fiefs_by_world(self, world_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM fiefs WHERE world_id=%s ORDER BY id;",
            (int(world_id),),
        )

    def list_early_tick_votes(self, world_id: int) -> list[int]:
        rows = self._fetchall(
            "SELECT user_id FROM early_tick_votes WHERE world_id=%s "
            "ORDER BY created_at, user_id;",
            (int(world_id),),
        )
        return [int(r["user_id"]) for r in rows]

    def add_early_tick_vote(self, world_id: int, user_id: int) -> bool:
        """True если голос новый."""
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO early_tick_votes (world_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT (world_id, user_id) DO NOTHING
                RETURNING user_id;
                """,
                (int(world_id), int(user_id)),
            )
            row = self.cursor.fetchone()
            self.commit()
            return row is not None

    def remove_early_tick_vote(self, world_id: int, user_id: int) -> bool:
        """True если голос был снят."""
        with self.lock:
            self.cursor.execute(
                """
                DELETE FROM early_tick_votes
                WHERE world_id=%s AND user_id=%s
                RETURNING user_id;
                """,
                (int(world_id), int(user_id)),
            )
            row = self.cursor.fetchone()
            self.commit()
            return row is not None

    def clear_early_tick_votes(self, world_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                "DELETE FROM early_tick_votes WHERE world_id=%s;",
                (int(world_id),),
            )
            self.commit()

    def update_fief(self, fief_id: int, **fields: Any) -> None:
        self._update("fiefs", fief_id, fields)

    def spend_fief_action(
        self,
        fief_id: int,
        *,
        last_active_at: datetime,
        last_active_tick: int,
    ) -> dict | None:
        """Атомарно списать 1 действие. None если actions < 1 или frozen."""
        with self.lock:
            try:
                self.cursor.execute(
                    """
                    UPDATE fiefs
                    SET actions = actions - 1,
                        last_active_at = %s,
                        last_active_tick = %s
                    WHERE id = %s
                      AND actions >= 1
                      AND frozen = false
                    RETURNING *;
                    """,
                    (last_active_at, int(last_active_tick), int(fief_id)),
                )
                row = self.cursor.fetchone()
                if not row:
                    self.commit()
                    return None
                cols = [d[0] for d in self.cursor.description]
                result = self._normalize(dict(zip(cols, row)))
                self.commit()
                return result
            except Exception:
                self._rollback_outside_tx()
                raise

    def debit_fief_resources(
        self,
        fief_id: int,
        amounts: Mapping[str, int] | None = None,
        **kwargs: int,
    ) -> dict | None:
        """Атомарно списать ресурсы из реестра. None если не хватает остатка."""
        normalized = normalize_debit_amounts(amounts, **kwargs)
        sql, params = build_debit_sql(normalized, fief_id)
        with self.lock:
            try:
                self.cursor.execute(sql, params)
                row = self.cursor.fetchone()
                if not row:
                    self.commit()
                    return None
                cols = [d[0] for d in self.cursor.description]
                result = self._normalize(dict(zip(cols, row)))
                self.commit()
                return result
            except Exception:
                self._rollback_outside_tx()
                raise

    def credit_fief_resources(
        self,
        fief_id: int,
        amounts: Mapping[str, int] | None = None,
        **kwargs: int,
    ) -> dict | None:
        """Атомарно начислить ресурсы из реестра."""
        normalized = normalize_credit_amounts(amounts, **kwargs)
        sql, params = build_credit_sql(normalized, fief_id)
        with self.lock:
            try:
                self.cursor.execute(sql, params)
                row = self.cursor.fetchone()
                if not row:
                    self.commit()
                    return None
                cols = [d[0] for d in self.cursor.description]
                result = self._normalize(dict(zip(cols, row)))
                self.commit()
                return result
            except Exception:
                self._rollback_outside_tx()
                raise

    def bump_event_action(
        self,
        event_id: int,
        fief_id: int,
        action_key: str,
        amount: int,
    ) -> None:
        """Вклад в котёл события без rollback при повторном вкладе."""
        with self.lock:
            try:
                self.cursor.execute(
                    """
                    INSERT INTO event_actions (event_id, fief_id, action_key, amount)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id, fief_id, action_key)
                    DO UPDATE SET amount = event_actions.amount + EXCLUDED.amount;
                    """,
                    (int(event_id), int(fief_id), action_key, int(amount)),
                )
                self.commit()
            except Exception:
                self._rollback_outside_tx()
                raise

    def set_fief_names_for_user(self, user_id: int, name: str) -> None:
        with self.lock:
            self.cursor.execute(
                "UPDATE fiefs SET name=%s WHERE user_id=%s;",
                (name, user_id),
            )
            self.commit()

    def touch_fief(self, fief_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                "UPDATE fiefs SET last_active_at=NOW() WHERE id=%s;",
                (fief_id,),
            )
            self.commit()

    # --- pacts ---
    def create_pact(self, realm_id: int, name: str, founder_fief_id: int) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO pacts (realm_id, name, founder_fief_id)
                VALUES (%s,%s,%s) RETURNING *;
                """,
                (realm_id, name, founder_fief_id),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            pact = dict(zip(cols, row))
            self.cursor.execute(
                "UPDATE fiefs SET pact_id=%s, cover_allies=FALSE WHERE id=%s;",
                (pact["id"], founder_fief_id),
            )
            self.commit()
            return pact

    def get_pact(self, pact_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM pacts WHERE id=%s;", (pact_id,))

    def update_pact(self, pact_id: int, **fields: Any) -> None:
        self._update("pacts", pact_id, fields)

    def pact_members(self, pact_id: int) -> list[dict]:
        return self._fetchall("SELECT * FROM fiefs WHERE pact_id=%s;", (pact_id,))

    def dissolve_pact(self, pact_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                "UPDATE pact_invites SET status='cancelled' "
                "WHERE pact_id=%s AND status='open';",
                (pact_id,),
            )
            self.cursor.execute("UPDATE fiefs SET pact_id=NULL WHERE pact_id=%s;", (pact_id,))
            self.cursor.execute("DELETE FROM pacts WHERE id=%s;", (pact_id,))
            self.commit()

    def create_pact_invite(self, **fields: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO pact_invites (
                    realm_id, pact_id, inviter_fief_id, target_fief_id,
                    expires_at, expires_tick
                ) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *;
                """,
                (
                    fields["realm_id"],
                    fields["pact_id"],
                    fields["inviter_fief_id"],
                    fields["target_fief_id"],
                    fields.get("expires_at") or (_utcnow() + timedelta(days=3650)),
                    fields["expires_tick"],
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def get_pact_invite(self, invite_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM pact_invites WHERE id=%s;", (invite_id,))

    def get_open_pact_invite(self, pact_id: int, target_fief_id: int) -> dict | None:
        return self._fetchone(
            """
            SELECT i.* FROM pact_invites i
            JOIN realms r ON r.id = i.realm_id
            WHERE i.pact_id=%s AND i.target_fief_id=%s AND i.status='open'
              AND i.expires_tick > r.tick_index;
            """,
            (pact_id, target_fief_id),
        )

    def claim_open_pact_invite(self, invite_id: int, new_status: str) -> dict | None:
        """Атомарно open→new_status. None если приглашение уже не open."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE pact_invites i SET status=%s
                FROM realms r
                WHERE i.id=%s AND i.status='open' AND r.id = i.realm_id
                  AND i.expires_tick > r.tick_index
                RETURNING i.*;
                """,
                (new_status, invite_id),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def update_pact_invite(self, invite_id: int, **fields: Any) -> None:
        self._update("pact_invites", invite_id, fields)

    # --- trade ---
    def create_trade(self, **fields: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO trade_offers (
                    realm_id, offerer_fief_id, target_fief_id,
                    give_res, give_amt, want_res, want_amt, expires_at, expires_tick
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *;
                """,
                (
                    fields["realm_id"],
                    fields["offerer_fief_id"],
                    fields.get("target_fief_id"),
                    fields["give_res"],
                    fields["give_amt"],
                    fields["want_res"],
                    fields["want_amt"],
                    fields.get("expires_at") or (_utcnow() + timedelta(days=3650)),
                    fields["expires_tick"],
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def list_open_trades(self, realm_id: int, for_fief_id: int | None = None) -> list[dict]:
        """Открытые лоты континента (все долины того же world_id)."""
        realm = self.get_realm(realm_id)
        world_id = realm.get("world_id") if realm else None
        if world_id is None:
            if for_fief_id is None:
                return self._fetchall(
                    """
                    SELECT t.* FROM trade_offers t
                    JOIN realms r ON r.id = t.realm_id
                    WHERE t.realm_id=%s AND t.status='open'
                      AND t.expires_tick > r.tick_index
                      AND t.target_fief_id IS NULL
                    ORDER BY t.id DESC;
                    """,
                    (realm_id,),
                )
            return self._fetchall(
                """
                SELECT t.* FROM trade_offers t
                JOIN realms r ON r.id = t.realm_id
                WHERE t.realm_id=%s AND t.status='open'
                  AND t.expires_tick > r.tick_index
                  AND (t.target_fief_id IS NULL OR t.target_fief_id=%s
                       OR t.offerer_fief_id=%s)
                ORDER BY t.id DESC;
                """,
                (realm_id, for_fief_id, for_fief_id),
            )
        if for_fief_id is None:
            return self._fetchall(
                """
                SELECT t.* FROM trade_offers t
                JOIN realms r ON r.id = t.realm_id
                WHERE r.world_id=%s AND t.status='open'
                  AND t.expires_tick > r.tick_index
                  AND t.target_fief_id IS NULL
                ORDER BY t.id DESC;
                """,
                (int(world_id),),
            )
        return self._fetchall(
            """
            SELECT t.* FROM trade_offers t
            JOIN realms r ON r.id = t.realm_id
            WHERE r.world_id=%s AND t.status='open'
              AND t.expires_tick > r.tick_index
              AND (t.target_fief_id IS NULL OR t.target_fief_id=%s
                   OR t.offerer_fief_id=%s)
            ORDER BY t.id DESC;
            """,
            (int(world_id), for_fief_id, for_fief_id),
        )

    def list_expired_open_trades(self, realm_id: int, tick_index: int) -> list[dict]:
        return self._fetchall(
            """
            SELECT * FROM trade_offers
            WHERE realm_id=%s AND status='open' AND expires_tick <= %s;
            """,
            (realm_id, tick_index),
        )

    def get_trade(self, trade_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM trade_offers WHERE id=%s;", (trade_id,))

    def claim_open_trade(self, trade_id: int) -> dict | None:
        """Атомарно open→done. None если лот уже не open (безопасный no-op)."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE trade_offers SET status='done'
                WHERE id=%s AND status='open'
                RETURNING *;
                """,
                (trade_id,),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def claim_cancel_open_trade(self, trade_id: int) -> dict | None:
        """Атомарно open→cancelled. None если лот уже не open (без двойного возврата)."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE trade_offers SET status='cancelled'
                WHERE id=%s AND status='open'
                RETURNING *;
                """,
                (trade_id,),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def update_trade(self, trade_id: int, **fields: Any) -> None:
        self._update("trade_offers", trade_id, fields)

    # --- raids ---
    def log_raid(self, **fields: Any) -> dict:
        with self.lock:
            attacker_realm = fields["realm_id"]
            victim_realm = fields.get("victim_realm_id", attacker_realm)
            loot_defs = raid_lootable_defs()
            stolen_cols = [r.raid_stolen_column for r in loot_defs if r.raid_stolen_column]
            stolen_vals = [
                int(fields.get(col, 0) or 0) for col in stolen_cols
            ]
            col_sql = ", ".join(stolen_cols)
            ph_sql = ", ".join(["%s"] * len(stolen_cols))
            self.cursor.execute(
                f"""
                INSERT INTO raids_log (
                    realm_id, victim_realm_id, attacker_fief_id, victim_fief_id,
                    success, might_spent, {col_sql}, public_line,
                    tick_index
                ) VALUES (%s,%s,%s,%s,%s,%s,{ph_sql},%s,%s) RETURNING *;
                """,
                (
                    attacker_realm,
                    victim_realm,
                    fields["attacker_fief_id"],
                    fields["victim_fief_id"],
                    fields["success"],
                    fields["might_spent"],
                    *stolen_vals,
                    fields["public_line"],
                    fields.get("tick_index"),
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def count_raids_between(self, attacker_id: int, victim_id: int, since_tick: int) -> int:
        with self.lock:
            self.cursor.execute(
                """
                SELECT COUNT(*) FROM raids_log
                WHERE attacker_fief_id=%s AND victim_fief_id=%s
                  AND tick_index >= %s;
                """,
                (attacker_id, victim_id, since_tick),
            )
            return int(self.cursor.fetchone()[0])

    def last_raid_attacker_victim(self, attacker_id: int, victim_id: int) -> int | None:
        """Тик последнего набега пары, либо None."""
        with self.lock:
            self.cursor.execute(
                """
                SELECT tick_index FROM raids_log
                WHERE attacker_fief_id=%s AND victim_fief_id=%s
                  AND tick_index IS NOT NULL
                ORDER BY id DESC LIMIT 1;
                """,
                (attacker_id, victim_id),
            )
            row = self.cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None

    def raids_since_tick(self, realm_id: int, since_tick: int) -> list[dict]:
        """Набеги, где долина - атакующая или жертва (для кровной вражды)."""
        return self._fetchall(
            """
            SELECT * FROM raids_log
            WHERE tick_index >= %s
              AND (realm_id=%s OR victim_realm_id=%s)
            ORDER BY id;
            """,
            (since_tick, realm_id, realm_id),
        )

    # --- events ---
    def create_event(self, **fields: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO realm_events (
                    realm_id, kind, event_key, payload, narrative, status,
                    resolves_at, resolves_tick
                ) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s) RETURNING *;
                """,
                (
                    fields["realm_id"],
                    fields["kind"],
                    fields["event_key"],
                    json.dumps(fields.get("payload", {})),
                    fields.get("narrative"),
                    fields.get("status", "active"),
                    fields.get("resolves_at"),
                    fields.get("resolves_tick"),
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def get_active_events(self, realm_id: int, kind: str | None = None) -> list[dict]:
        if kind:
            return self._fetchall(
                "SELECT * FROM realm_events WHERE realm_id=%s AND status='active' AND kind=%s;",
                (realm_id, kind),
            )
        return self._fetchall(
            "SELECT * FROM realm_events WHERE realm_id=%s AND status='active';",
            (realm_id,),
        )

    def update_event(self, event_id: int, **fields: Any) -> None:
        payload = dict(fields)
        if "payload" in payload and not isinstance(payload["payload"], str):
            payload["payload"] = json.dumps(payload["payload"])
            # special path
            with self.lock:
                sets = []
                vals = []
                for k, v in payload.items():
                    if k == "payload":
                        sets.append("payload=%s::jsonb")
                    else:
                        sets.append(f"{k}=%s")
                    vals.append(v)
                vals.append(event_id)
                self.cursor.execute(
                    f"UPDATE realm_events SET {', '.join(sets)} WHERE id=%s;",
                    tuple(vals),
                )
                self.commit()
            return
        self._update("realm_events", event_id, fields)

    def get_event(self, event_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM realm_events WHERE id=%s;", (event_id,))

    # --- tile entities ---
    def create_tile_entity(self, **fields: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO tile_entities (
                    realm_id, x, y, kind, payload, status, created_tick, expires_tick
                ) VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s,%s) RETURNING *;
                """,
                (
                    fields["realm_id"],
                    fields["x"],
                    fields["y"],
                    fields["kind"],
                    json.dumps(fields.get("payload") or {}),
                    fields.get("status", "active"),
                    fields["created_tick"],
                    fields.get("expires_tick"),
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def list_active_tile_entities(self, realm_id: int) -> list[dict]:
        return self._fetchall(
            """
            SELECT * FROM tile_entities
            WHERE realm_id=%s AND status='active'
            ORDER BY y, x, id;
            """,
            (realm_id,),
        )

    def list_tile_entities_at(
        self,
        realm_id: int,
        x: int,
        y: int,
        *,
        active_only: bool = True,
    ) -> list[dict]:
        if active_only:
            return self._fetchall(
                """
                SELECT * FROM tile_entities
                WHERE realm_id=%s AND x=%s AND y=%s AND status='active'
                ORDER BY id;
                """,
                (realm_id, x, y),
            )
        return self._fetchall(
            """
            SELECT * FROM tile_entities
            WHERE realm_id=%s AND x=%s AND y=%s
            ORDER BY id;
            """,
            (realm_id, x, y),
        )

    def update_tile_entity(self, entity_id: int, **fields: Any) -> None:
        payload = dict(fields)
        if "payload" in payload and not isinstance(payload["payload"], str):
            payload["payload"] = json.dumps(payload["payload"])
            with self.lock:
                sets = []
                vals = []
                for k, v in payload.items():
                    if k == "payload":
                        sets.append("payload=%s::jsonb")
                    else:
                        sets.append(f"{k}=%s")
                    vals.append(v)
                vals.append(entity_id)
                self.cursor.execute(
                    f"UPDATE tile_entities SET {', '.join(sets)} WHERE id=%s;",
                    tuple(vals),
                )
                self.commit()
            return
        self._update("tile_entities", entity_id, fields)

    def claim_expire_tile_entity(self, entity_id: int) -> dict | None:
        """Атомарно active→expired. None если строка уже не active."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE tile_entities SET status='expired'
                WHERE id=%s AND status='active'
                RETURNING *;
                """,
                (entity_id,),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def delete_tile_entity(self, entity_id: int) -> None:
        with self.lock:
            self.cursor.execute(
                "DELETE FROM tile_entities WHERE id=%s;",
                (entity_id,),
            )
            self.commit()

    # --- action intents (declare-then-resolve для набегов) ---
    def get_action_intent(self, intent_id: int) -> dict | None:
        return self._fetchone(
            "SELECT * FROM action_intents WHERE id=%s;",
            (int(intent_id),),
        )

    def create_action_intent(self, **fields: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO action_intents (
                    world_id, tick_index, fief_id, kind, payload, status
                ) VALUES (%s,%s,%s,%s,%s::jsonb,%s) RETURNING *;
                """,
                (
                    fields["world_id"],
                    fields["tick_index"],
                    fields["fief_id"],
                    fields["kind"],
                    json.dumps(fields.get("payload") or {}),
                    fields.get("status", "open"),
                ),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def list_open_action_intents(
        self, world_id: int, tick_index: int
    ) -> list[dict]:
        return self._fetchall(
            """
            SELECT * FROM action_intents
            WHERE world_id=%s AND tick_index=%s AND status='open'
            ORDER BY id;
            """,
            (int(world_id), int(tick_index)),
        )

    def list_raid_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        return self._fetchall(
            f"""
            SELECT * FROM action_intents
            WHERE world_id=%s AND tick_index=%s AND kind='raid'
              AND status IN ({placeholders})
            ORDER BY id;
            """,
            (int(world_id), int(tick_index), *statuses),
        )

    def list_open_raid_intents_for_fief(self, fief_id: int) -> list[dict]:
        return self._fetchall(
            """
            SELECT * FROM action_intents
            WHERE fief_id=%s AND kind='raid' AND status IN ('open', 'locked')
            ORDER BY id;
            """,
            (int(fief_id),),
        )

    def list_caravan_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        return self._fetchall(
            f"""
            SELECT * FROM action_intents
            WHERE world_id=%s AND tick_index=%s AND kind='caravan'
              AND status IN ({placeholders})
            ORDER BY id;
            """,
            (int(world_id), int(tick_index), *statuses),
        )

    def list_road_caravan_intents_for_fief(self, fief_id: int) -> list[dict]:
        """Исходящие обозы в пути: open и locked (ещё не доставлены / не отменены)."""
        return self._fetchall(
            """
            SELECT * FROM action_intents
            WHERE fief_id=%s AND kind='caravan' AND status IN ('open', 'locked')
            ORDER BY id;
            """,
            (int(fief_id),),
        )

    def list_recent_caravan_receiver_ids(
        self, fief_id: int, *, limit: int = 8
    ) -> list[int]:
        """Недавние получатели обозов (любой статус), новые сверху, без дублей."""
        rows = self._fetchall(
            """
            SELECT payload->>'receiver_id' AS receiver_id
            FROM action_intents
            WHERE fief_id=%s AND kind='caravan'
              AND payload ? 'receiver_id'
            ORDER BY id DESC
            LIMIT 40;
            """,
            (int(fief_id),),
        )
        out: list[int] = []
        seen: set[int] = set()
        for row in rows:
            try:
                rid = int(row.get("receiver_id") or 0)
            except (TypeError, ValueError):
                continue
            if rid <= 0 or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
            if len(out) >= int(limit):
                break
        return out

    def list_cover_stance_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]:
        if not statuses:
            return []
        placeholders = ", ".join(["%s"] * len(statuses))
        return self._fetchall(
            f"""
            SELECT * FROM action_intents
            WHERE world_id=%s AND tick_index=%s AND kind='cover_stance'
              AND status IN ({placeholders})
            ORDER BY id;
            """,
            (int(world_id), int(tick_index), *statuses),
        )

    def list_open_cover_stance_intents_for_fief(self, fief_id: int) -> list[dict]:
        return self._fetchall(
            """
            SELECT * FROM action_intents
            WHERE fief_id=%s AND kind='cover_stance'
              AND status IN ('open', 'locked')
            ORDER BY id;
            """,
            (int(fief_id),),
        )

    def lock_action_intents(
        self, world_id: int, tick_index: int, *, kind: str = "raid"
    ) -> int:
        """open→locked для заявок мира на тик. Возвращает число строк."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE action_intents SET status='locked'
                WHERE world_id=%s AND tick_index=%s AND kind=%s AND status='open'
                RETURNING id;
                """,
                (int(world_id), int(tick_index), str(kind)),
            )
            rows = self.cursor.fetchall() or []
            self.commit()
            return len(rows)

    def claim_resolve_action_intent(self, intent_id: int) -> dict | None:
        """Атомарно locked→resolved (или open→resolved на force-lock path)."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE action_intents SET status='resolved'
                WHERE id=%s AND status IN ('open', 'locked')
                RETURNING *;
                """,
                (int(intent_id),),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def cancel_action_intent(
        self,
        intent_id: int,
        *,
        statuses: tuple[str, ...] = ("open",),
    ) -> dict | None:
        """CAS: open→cancelled по умолчанию; night loot может снять и locked."""
        if not statuses:
            return None
        placeholders = ", ".join(["%s"] * len(statuses))
        with self.lock:
            self.cursor.execute(
                f"""
                UPDATE action_intents SET status='cancelled'
                WHERE id=%s AND status IN ({placeholders})
                RETURNING *;
                """,
                (int(intent_id), *statuses),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def update_action_intent_payload(
        self, intent_id: int, payload: dict
    ) -> None:
        with self.lock:
            self.cursor.execute(
                """
                UPDATE action_intents SET payload=%s::jsonb
                WHERE id=%s;
                """,
                (json.dumps(payload or {}), int(intent_id)),
            )
            self.commit()

    def update_open_action_intent_payload(
        self, intent_id: int, payload: dict
    ) -> dict | None:
        """CAS: payload пока заявка в пути (open или locked)."""
        with self.lock:
            self.cursor.execute(
                """
                UPDATE action_intents SET payload=%s::jsonb
                WHERE id=%s AND status IN ('open', 'locked')
                RETURNING *;
                """,
                (json.dumps(payload or {}), int(intent_id)),
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return self._normalize(dict(zip(cols, row)))

    def event_actions(self, event_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM event_actions WHERE event_id=%s;",
            (event_id,),
        )

    def next_decree_number(self, realm_id: int | None) -> int:
        with self.lock:
            if realm_id is None:
                self.cursor.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM decrees;")
            else:
                self.cursor.execute(
                    "SELECT COALESCE(MAX(number), 0) + 1 FROM decrees WHERE realm_id=%s;",
                    (realm_id,),
                )
            return int(self.cursor.fetchone()[0])

    def add_decree(self, realm_id: int | None, number: int, body: str) -> dict:
        with self.lock:
            self.cursor.execute(
                "INSERT INTO decrees (realm_id, number, body) VALUES (%s,%s,%s) RETURNING *;",
                (realm_id, number, body),
            )
            row = self.cursor.fetchone()
            cols = [d[0] for d in self.cursor.description]
            self.commit()
            return dict(zip(cols, row))

    def list_announced_patch_names(self) -> set[str]:
        with self.lock:
            self.cursor.execute("SELECT name FROM announced_patches;")
            rows = self.cursor.fetchall() or []
            return {str(row[0]) for row in rows}

    def mark_patch_announced(self, name: str) -> None:
        """Фиксирует вестник после успешной (или пустой) рассылки."""
        with self.lock:
            self.cursor.execute(
                "INSERT INTO announced_patches (name) VALUES (%s) "
                "ON CONFLICT (name) DO NOTHING;",
                (str(name),),
            )
            self.commit()

    # --- helpers ---
    def _update(self, table: str, row_id: int, fields: dict) -> None:
        if not fields:
            return
        cols = []
        vals = []
        for k, v in fields.items():
            cols.append(f"{k}=%s")
            vals.append(v)
        vals.append(row_id)
        with self.lock:
            try:
                self.cursor.execute(
                    f"UPDATE {table} SET {', '.join(cols)} WHERE id=%s;",
                    tuple(vals),
                )
                self.commit()
            except Exception:
                self._rollback_outside_tx()
                raise

    def _fetchone(self, sql: str, args: tuple = ()) -> dict | None:
        with self.lock:
            try:
                self.cursor.execute(sql, args)
                row = self.cursor.fetchone()
                if not row:
                    # Закрываем неявную read-транзакцию вне transaction().
                    self.commit()
                    return None
                cols = [d[0] for d in self.cursor.description]
                result = self._normalize(dict(zip(cols, row)))
                self.commit()
                return result
            except Exception:
                self._rollback_outside_tx()
                raise

    def _fetchall(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            try:
                self.cursor.execute(sql, args)
                rows = self.cursor.fetchall()
                cols = [d[0] for d in self.cursor.description]
                result = [self._normalize(dict(zip(cols, r))) for r in rows]
                self.commit()
                return result
            except Exception:
                self._rollback_outside_tx()
                raise

    def _normalize(self, row: dict) -> dict:
        for k, v in list(row.items()):
            if isinstance(v, str) and k in (
                "feature_flags",
                "balance_overrides",
                "pending_raid_lines",
                "last_rumor_lines",
                "rumor_queue",
                "payload",
            ):
                try:
                    row[k] = json.loads(v)
                except Exception:
                    pass
        return row


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_db() -> Database:
    global _db
    _db = Database()
    return _db
