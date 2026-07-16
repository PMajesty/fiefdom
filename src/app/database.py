"""PostgreSQL: схема и доступ к данным Вотчины."""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import pg8000

from app.config import DB_CONFIG, tick_slots
from app.domain.tick_schedule import LEGACY_TWO_TICK_SLOTS, remap_last_tick_slot

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Database:
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
                    wipe_confirm_code TEXT,
                    wipe_confirm_until TIMESTAMPTZ,
                    forced_tick_count INT NOT NULL DEFAULT 0
                );
                """,
                """
                CREATE TABLE IF NOT EXISTS fiefs (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL REFERENCES users(telegram_id),
                    name TEXT NOT NULL,
                    grain INT NOT NULL DEFAULT 0,
                    goods INT NOT NULL DEFAULT 0,
                    might INT NOT NULL DEFAULT 0,
                    pending_grain DOUBLE PRECISION NOT NULL DEFAULT 0,
                    pending_goods DOUBLE PRECISION NOT NULL DEFAULT 0,
                    pending_might DOUBLE PRECISION NOT NULL DEFAULT 0,
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
                    cover_allies BOOLEAN NOT NULL DEFAULT TRUE,
                    frozen BOOLEAN NOT NULL DEFAULT FALSE,
                    UNIQUE (realm_id, user_id),
                    UNIQUE (user_id)
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
                """
                CREATE TABLE IF NOT EXISTS raids_log (
                    id BIGSERIAL PRIMARY KEY,
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    attacker_fief_id BIGINT NOT NULL,
                    victim_fief_id BIGINT NOT NULL,
                    success BOOLEAN NOT NULL,
                    might_spent INT NOT NULL,
                    grain_stolen INT NOT NULL DEFAULT 0,
                    goods_stolen INT NOT NULL DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS tick_force_votes (
                    realm_id BIGINT NOT NULL REFERENCES realms(id) ON DELETE CASCADE,
                    fief_id BIGINT NOT NULL REFERENCES fiefs(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (realm_id, fief_id)
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
                CREATE INDEX IF NOT EXISTS idx_tick_force_votes_realm
                    ON tick_force_votes(realm_id);
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
                "ALTER TABLE trade_offers ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE pact_invites ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE personal_deals ADD COLUMN IF NOT EXISTS expires_tick INT;",
                "ALTER TABLE realm_events ADD COLUMN IF NOT EXISTS resolves_tick INT;",
                "ALTER TABLE raids_log ADD COLUMN IF NOT EXISTS tick_index INT;",
            ):
                self.cursor.execute(stmt)
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
        self.cursor.execute(
            """
            WITH open_lots AS (
                SELECT id, offerer_fief_id, give_res, give_amt
                FROM trade_offers
                WHERE status = 'open'
                FOR UPDATE
            ),
            totals AS (
                SELECT
                    offerer_fief_id,
                    COALESCE(
                        SUM(give_amt) FILTER (WHERE give_res = 'grain'), 0
                    )::INT AS grain_amt,
                    COALESCE(
                        SUM(give_amt) FILTER (WHERE give_res = 'goods'), 0
                    )::INT AS goods_amt
                FROM open_lots
                GROUP BY offerer_fief_id
            ),
            refunded AS (
                UPDATE fiefs AS f
                SET
                    grain = f.grain + t.grain_amt,
                    goods = f.goods + t.goods_amt
                FROM totals AS t
                WHERE f.id = t.offerer_fief_id
            )
            UPDATE trade_offers AS t
            SET status = 'cancelled'
            FROM open_lots AS o
            WHERE t.id = o.id;
            """
        )
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
                forced_tick_count INT NOT NULL DEFAULT 0,
                wipe_confirm_code TEXT,
                wipe_confirm_until TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS world_id BIGINT "
            "REFERENCES worlds(id);"
        )
        self.cursor.execute(
            "ALTER TABLE realms ADD COLUMN IF NOT EXISTS chain_index INT;"
        )
        self.cursor.execute(
            "ALTER TABLE tick_force_votes ADD COLUMN IF NOT EXISTS world_id BIGINT "
            "REFERENCES worlds(id) ON DELETE CASCADE;"
        )
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
            UPDATE tick_force_votes v
            SET world_id = r.world_id
            FROM realms r
            WHERE v.realm_id = r.id AND v.world_id IS NULL;
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
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_fiefs_user_id
            ON fiefs(user_id);
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
                INSERT INTO worlds (name) VALUES ('Континент') RETURNING *;
                """
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
        """Зеркалит континентальные часы/события на все долины мира."""
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
                WHERE w.id = %s AND realms.world_id = w.id;
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
    ) -> dict:
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO realms (
                    chat_id, title, width, height, timezone, tick_hour, tick_minute,
                    feature_flags, next_catastrophe_tick, tick_index, day_number,
                    world_id, chain_index, last_tick_local_date, last_tick_slot,
                    next_catastrophe_key, pending_minor_key, active_minor_key
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s
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

    def update_tile(self, tile_id: int, **fields: Any) -> None:
        self._update("map_tiles", tile_id, fields)

    def fief_tiles(self, fief_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM map_tiles WHERE owner_fief_id=%s ORDER BY y, x;",
            (fief_id,),
        )

    # --- fiefs ---
    def create_fief(self, realm_id: int, user_id: int, name: str, **resources: Any) -> dict:
        with self.lock:
            self.cursor.execute(
                "SELECT tick_index FROM realms WHERE id=%s;",
                (realm_id,),
            )
            row_tick = self.cursor.fetchone()
            tick_index = int(row_tick[0]) if row_tick else 0
            self.cursor.execute(
                """
                INSERT INTO fiefs (
                    realm_id, user_id, name, grain, goods, might, actions,
                    onboard_step, last_active_tick
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *;
                """,
                (
                    realm_id,
                    user_id,
                    name,
                    resources.get("grain", 0),
                    resources.get("goods", 0),
                    resources.get("might", 0),
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

    def update_fief(self, fief_id: int, **fields: Any) -> None:
        self._update("fiefs", fief_id, fields)

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
                "UPDATE fiefs SET pact_id=%s WHERE id=%s;",
                (pact["id"], founder_fief_id),
            )
            self.commit()
            return pact

    def get_pact(self, pact_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM pacts WHERE id=%s;", (pact_id,))

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
            self.cursor.execute(
                """
                INSERT INTO raids_log (
                    realm_id, victim_realm_id, attacker_fief_id, victim_fief_id,
                    success, might_spent, grain_stolen, goods_stolen, public_line,
                    tick_index
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *;
                """,
                (
                    attacker_realm,
                    victim_realm,
                    fields["attacker_fief_id"],
                    fields["victim_fief_id"],
                    fields["success"],
                    fields["might_spent"],
                    fields.get("grain_stolen", 0),
                    fields.get("goods_stolen", 0),
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

    def add_event_action(self, event_id: int, fief_id: int, action_key: str = "default", amount: int = 0) -> bool:
        """True если впервые записано."""
        with self.lock:
            try:
                self.cursor.execute(
                    """
                    INSERT INTO event_actions (event_id, fief_id, action_key, amount)
                    VALUES (%s,%s,%s,%s);
                    """,
                    (event_id, fief_id, action_key, amount),
                )
                self.commit()
                return True
            except Exception:
                self.rollback()
                return False

    def get_event(self, event_id: int) -> dict | None:
        return self._fetchone("SELECT * FROM realm_events WHERE id=%s;", (event_id,))

    def event_actions(self, event_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM event_actions WHERE event_id=%s;",
            (event_id,),
        )

    def add_force_tick_vote(self, realm_id: int, fief_id: int) -> bool:
        """True если голос записан впервые. Голос континентальный (world_id)."""
        realm = self.get_realm(realm_id)
        world_id = int(realm["world_id"]) if realm and realm.get("world_id") else None
        with self.lock:
            self.cursor.execute(
                """
                INSERT INTO tick_force_votes (realm_id, fief_id, world_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (realm_id, fief_id) DO NOTHING
                RETURNING fief_id;
                """,
                (int(realm_id), int(fief_id), world_id),
            )
            row = self.cursor.fetchone()
            self.commit()
            return row is not None

    def list_force_tick_votes(self, realm_id: int) -> list[dict]:
        """Совместимость: голоса долины. Для континента используйте list_world_force_tick_votes."""
        return self._fetchall(
            "SELECT * FROM tick_force_votes WHERE realm_id=%s;",
            (int(realm_id),),
        )

    def list_world_force_tick_votes(self, world_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM tick_force_votes WHERE world_id=%s;",
            (int(world_id),),
        )

    def clear_force_tick_votes(self, realm_id: int) -> int:
        """Стирает голоса долины. Возвращает число удалённых строк."""
        with self.lock:
            self.cursor.execute(
                "DELETE FROM tick_force_votes WHERE realm_id=%s RETURNING fief_id;",
                (int(realm_id),),
            )
            rows = self.cursor.fetchall()
            self.commit()
            return len(rows)

    def clear_world_force_tick_votes(self, world_id: int) -> int:
        with self.lock:
            self.cursor.execute(
                "DELETE FROM tick_force_votes WHERE world_id=%s RETURNING fief_id;",
                (int(world_id),),
            )
            rows = self.cursor.fetchall()
            self.commit()
            return len(rows)

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
