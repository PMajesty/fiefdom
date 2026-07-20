#!/usr/bin/env python3
"""Разовый ручной рефанд переплаты за клетки.

Считает delta = sum(OLD_CLAIM_COSTS_V1[i] - CLAIM_COSTS[i]) для i=2..N,
где N - число активных (не заросших) клеток усадьбы.
Учитывает только базовую кривую: старый ×2 за глушь в истории клеймов
не восстанавливается (в БД нет состава клеток на момент оплаты).

Начисляет товары без обрезки склада (overflow допустим).

Не вызывается при старте бота.

Порядок (обязательно) - код уже на VPS, бот остановлен, клеймов нет:

  # с рабочей машины: выгрузка без restart
  python deploy/quick_deploy.py --skip-restart

  # на VPS
  sudo systemctl stop fiefdom
  cd /opt/fiefdom
  PYTHONPATH=src ./venv/bin/python deploy/claim_cost_refund.py --dry-run
  PYTHONPATH=src ./venv/bin/python deploy/claim_cost_refund.py --apply
  sudo systemctl start fiefdom

Пока бот остановлен, N клеток = оплата по старой кривой; скрипт считает
дельту по новым CLAIM_COSTS из выгруженного src. Не делайте --apply при
живом боте на новой кривой: свежие клеймы попадут в N и будут переплачены.

Идемпотентность: после --apply пишет applied_patches.name=claim_cost_refund_v1.
Повторный --apply без --force откажется.
--force снимает метку и начисляет снова (двойная выплата - только осознанно).
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(SCRIPT_DIR / "secrets.env")
load_dotenv(PROJECT_ROOT / ".env")

from app import balance as B
from app.database import Database

PATCH_NAME = "claim_cost_refund_v1"

# (fief_id, realm_id, active_tiles, delta_goods)
RefundPlanRow = tuple[int, int, int, int]


def active_tile_count(tiles: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for t in tiles if not t.get("is_overgrown"))


def plan_from_fief_tile_counts(
    rows: Sequence[tuple[int, int, int]],
) -> list[RefundPlanRow]:
    """[(fief_id, realm_id, active_tiles), ...] → строки с delta > 0."""
    planned: list[RefundPlanRow] = []
    for fief_id, realm_id, active in rows:
        delta = B.claim_cost_refund_delta(int(active))
        if delta > 0:
            planned.append((int(fief_id), int(realm_id), int(active), delta))
    return planned


def plan_refunds(db: Database) -> list[RefundPlanRow]:
    db.cursor.execute("SELECT id, realm_id FROM fiefs ORDER BY id;")
    rows = db.cursor.fetchall() or []
    counts: list[tuple[int, int, int]] = []
    for fief_id, realm_id in rows:
        tiles = db.fief_tiles(int(fief_id))
        counts.append((int(fief_id), int(realm_id), active_tile_count(tiles)))
    return plan_from_fief_tile_counts(counts)


def ensure_applied_patches_table(db: Database) -> None:
    db.cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS applied_patches (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )


def patch_already_applied(db: Database) -> bool:
    ensure_applied_patches_table(db)
    db.cursor.execute(
        "SELECT 1 FROM applied_patches WHERE name=%s;",
        (PATCH_NAME,),
    )
    return db.cursor.fetchone() is not None


def apply_refunds(
    db: Database,
    planned: list[RefundPlanRow],
    *,
    replace_marker: bool = False,
) -> None:
    with db.transaction():
        ensure_applied_patches_table(db)
        if replace_marker:
            db.cursor.execute(
                "DELETE FROM applied_patches WHERE name=%s;",
                (PATCH_NAME,),
            )
        for fief_id, _realm_id, _active, delta in planned:
            updated = db.credit_fief_resources(fief_id, goods=delta)
            if updated is None:
                raise RuntimeError(f"credit failed for fief_id={fief_id}")
        db.cursor.execute(
            "INSERT INTO applied_patches (name) VALUES (%s);",
            (PATCH_NAME,),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="только план")
    mode.add_argument("--apply", action="store_true", help="начислить товары")
    parser.add_argument(
        "--force",
        action="store_true",
        help="снять метку и начислить снова (двойная выплата)",
    )
    args = parser.parse_args()

    db = Database()
    try:
        already = patch_already_applied(db)
        if already and args.apply and not args.force:
            print(f"ABORT: {PATCH_NAME} уже в applied_patches. Нужен --force?")
            return 2

        planned = plan_refunds(db)
        total = sum(d for *_rest, d in planned)
        print(f"fiefs with refund: {len(planned)}")
        print(f"total goods: {total}")
        for fief_id, realm_id, active, delta in planned:
            print(
                f"  fief={fief_id} realm={realm_id} "
                f"tiles={active} +{delta} goods"
            )
        if args.dry_run:
            print("dry-run only; no writes")
            return 0

        if not planned:
            print("nothing to credit; marking patch anyway")
        apply_refunds(db, planned, replace_marker=bool(args.force and already))
        print(f"applied {PATCH_NAME}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
