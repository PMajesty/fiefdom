"""SQL/DDL-хелперы колонок ресурсов (persistence-слой, не domain)."""
from __future__ import annotations

from collections.abc import Mapping

from app.domain import resource_registry as res_mod



def raid_stolen_column_map() -> dict[str, str]:
    """resource key → колонка raids_log (*_stolen)."""
    return {
        r.key: r.raid_stolen_column
        for r in res_mod.raid_lootable_defs()
        if r.raid_stolen_column
    }


def raid_stolen_fields(stolen: Mapping[str, int]) -> dict[str, int]:
    """Bag добычи → kwargs колонок raids_log."""
    return {
        col: int(stolen.get(key, 0) or 0)
        for key, col in raid_stolen_column_map().items()
    }


def fief_stash_ddl_lines() -> list[str]:
    lines: list[str] = []
    for r in res_mod.RESOURCE_DEFS:
        lines.append(f"{r.key} INT NOT NULL DEFAULT 0")
        lines.append(f"{r.pending_column} DOUBLE PRECISION NOT NULL DEFAULT 0")
    return lines


def raids_stolen_ddl_lines() -> list[str]:
    return [
        f"{r.raid_stolen_column} INT NOT NULL DEFAULT 0"
        for r in res_mod.raid_lootable_defs()
        if r.raid_stolen_column
    ]


def ensure_resource_columns_sql() -> list[str]:
    """Идемпотентные ADD COLUMN для stash/pending и loot-колонок."""
    stmts: list[str] = []
    for r in res_mod.RESOURCE_DEFS:
        stmts.append(
            f"ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS "
            f"{r.key} INT NOT NULL DEFAULT 0;"
        )
        stmts.append(
            f"ALTER TABLE fiefs ADD COLUMN IF NOT EXISTS "
            f"{r.pending_column} DOUBLE PRECISION NOT NULL DEFAULT 0;"
        )
    for r in res_mod.raid_lootable_defs():
        stmts.append(
            f"ALTER TABLE raids_log ADD COLUMN IF NOT EXISTS "
            f"{r.raid_stolen_column} INT NOT NULL DEFAULT 0;"
        )
    return stmts


def build_debit_sql(normalized: Mapping[str, int], fief_id: int) -> tuple[str, tuple]:
    sets: list[str] = []
    set_vals: list[int] = []
    conds = ["id=%s"]
    cond_vals: list[int] = [int(fief_id)]
    for col, amt in normalized.items():
        sets.append(f"{col} = {col} - %s")
        set_vals.append(amt)
        conds.append(f"{col} >= %s")
        cond_vals.append(amt)
    sql = (
        f"UPDATE fiefs SET {', '.join(sets)} "
        f"WHERE {' AND '.join(conds)} RETURNING *;"
    )
    return sql, tuple(set_vals + cond_vals)


def build_credit_sql(normalized: Mapping[str, int], fief_id: int) -> tuple[str, tuple]:
    sets: list[str] = []
    set_vals: list[int] = []
    for col, amt in normalized.items():
        sets.append(f"{col} = {col} + %s")
        set_vals.append(amt)
    sql = (
        f"UPDATE fiefs SET {', '.join(sets)} "
        f"WHERE id=%s RETURNING *;"
    )
    return sql, tuple(set_vals + [int(fief_id)])


def build_annul_open_trades_sql() -> str:
    """Возврат эскроу по tradeable-ресурсам из реестра (патч applied_patches)."""
    tradeable = res_mod.tradeable_keys()
    sum_parts = ",\n                    ".join(
        (
            f"COALESCE(\n"
            f"                        SUM(give_amt) FILTER (WHERE give_res = '{key}'), 0\n"
            f"                    )::INT AS {key}_amt"
        )
        for key in tradeable
    )
    set_parts = ",\n                    ".join(
        f"{key} = f.{key} + t.{key}_amt" for key in tradeable
    )
    return f"""
            WITH open_lots AS (
                SELECT id, offerer_fief_id, give_res, give_amt
                FROM trade_offers
                WHERE status = 'open'
                FOR UPDATE
            ),
            totals AS (
                SELECT
                    offerer_fief_id,
                    {sum_parts}
                FROM open_lots
                GROUP BY offerer_fief_id
            ),
            refunded AS (
                UPDATE fiefs AS f
                SET
                    {set_parts}
                FROM totals AS t
                WHERE f.id = t.offerer_fief_id
            )
            UPDATE trade_offers AS t
            SET status = 'cancelled'
            FROM open_lots AS o
            WHERE t.id = o.id;
            """
