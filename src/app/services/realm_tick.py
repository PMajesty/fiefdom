"""Тик одной долины: absence, минор, экономика усадеб, digest."""
from __future__ import annotations

from app.repos import RealmTickRepos

import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import balance as B
from app.config import TIMEZONE
from app.domain import absence as absence_mod
from app.domain.digest import format_digest
from app.domain.production import TileView

from app.domain.event_apply import InstantMinorCtx, apply_instant_minor
from app.domain.events import (
    MINOR_EVENTS,
    event_digest_line,
    minor_effect,
    roll_minor_event,
)
from app.domain.tick import FiefTickState, apply_fief_tick
from app.domain.ticks import tick_active
from app.domain.tile_entities import (
    ActiveTileEntityRef,
    TileEntityResolveCtx,
    active_tile_entity_ref,
    resolve_realm_tile_entities,
)


class RealmTickRunner:
    def __init__(self, engine, db: RealmTickRepos | None = None) -> None:
        self._engine = engine
        self._db = db if db is not None else engine.db

    def apply_absence(self, realm_id: int) -> None:
        realm = self._db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        for fief in self._db.list_fiefs(realm_id):
            last_active_tick = fief.get("last_active_tick")
            ticks = (
                tick_index - int(last_active_tick)
                if last_active_tick is not None
                else B.OVERGROWN_TICKS
            )
            tier = absence_mod.inactivity_tier(ticks)
            if tier != "overgrown":
                continue
            tiles = self._db.fief_tiles(fief["id"])
            cores = [t for t in tiles if t.get("is_core")]
            if len(cores) < 1:
                # пометить первую как core
                if tiles:
                    self._db.update_tile(tiles[0]["id"], is_core=True)
                    cores = [tiles[0]]
            core_ids = {t["id"] for t in cores[:2]}
            for t in tiles:
                if t["id"] not in core_ids and not t.get("is_overgrown"):
                    self._db.update_tile(t["id"], is_overgrown=True)

    def run_realm_tick(
        self,
        realm_id: int,
        tick_slot: int | None = None,
        *,
        advance_clock: bool = True,
    ) -> dict:
        """Тик одной долины. При advance_clock=False часы уже выставлены миром."""
        if advance_clock:
            # Одиночный вызов (админ/тесты) гоняет весь континент.
            world_id = self._engine._world_id_for_realm(realm_id)
            world_result = self._engine.run_world_tick(world_id, tick_slot=tick_slot)
            for item in world_result.get("realms") or []:
                if int(item.get("realm_id") or 0) == int(realm_id):
                    return item
            if world_result.get("realms"):
                return world_result["realms"][0]
            return world_result

        realm = self._db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        tick_index = int(realm.get("tick_index") or 0)
        day = int(realm.get("day_number") or 1)
        self._engine.apply_absence(realm_id)
        entity_digest_lines, entity_refs = self._engine._resolve_tile_entities(
            realm_id, tick_index
        )

        event_line = self._engine._prepare_tick_minor(realm_id, consume_pending=False)
        realm = self._db.get_realm(realm_id) or realm
        base_farm_mult = self._engine.realm_modifiers(
            realm, tile_entities=entity_refs
        ).farm_mult()

        outcomes = []
        for fief in self._db.list_fiefs(realm_id):
            if fief.get("frozen"):
                continue
            tiles = [
                TileView(
                    x=t["x"],
                    y=t["y"],
                    tile_type=t["tile_type"],
                    owner_fief_id=t["owner_fief_id"],
                    building=t.get("building"),
                    building_level=int(t.get("building_level") or 0),
                    is_core=bool(t.get("is_core")),
                    is_overgrown=bool(t.get("is_overgrown")),
                )
                for t in self._db.fief_tiles(fief["id"])
            ]
            if not tick_active(fief.get("patrol_until_tick"), tick_index):
                if fief.get("patrol_until_tick") is not None or fief.get("patrol_until"):
                    self._db.update_fief(
                        fief["id"],
                        patrol_until=None,
                        patrol_until_tick=None,
                    )
            if not tick_active(fief.get("shield_until_tick"), tick_index):
                if fief.get("shield_until_tick") is not None or fief.get("shield_until"):
                    self._db.update_fief(
                        fief["id"],
                        shield_until=None,
                        shield_until_tick=None,
                    )

            # Неактивная долина владельца: без урожая и без +действия.
            if not self._engine.fief_is_active_play(fief):
                continue

            farm_mult = base_farm_mult

            state = FiefTickState.from_fief_row(
                fief,
                tiles,
                self._engine.barn_level(fief["id"]),
                farm_mult=farm_mult,
            )
            out = apply_fief_tick(state)
            self._db.update_fief(
                fief["id"],
                **out.balance_columns(),
                actions=out.actions,
                hungry=out.hungry,
            )
            outcomes.append((fief, out))

        feud_lines = self._engine._feud_lines(realm_id)
        raid_lines = list(realm.get("pending_raid_lines") or [])
        self._db.update_realm(realm_id, pending_raid_lines=[])

        tz = ZoneInfo(realm.get("timezone") or TIMEZONE)
        local_now = datetime.now(tz)
        local_date = local_now.date()

        sunday_extra = None
        if local_date.weekday() == 6:
            sunday_extra = self._engine._sunday_extra(realm_id)

        grow_msg = self._engine.maybe_grow_map(realm_id)
        realm = self._db.get_realm(realm_id) or realm
        digest = format_digest(
            realm_title=realm["title"],
            day=day,
            night_lines=raid_lines,
            event_line=event_line,
            feud_lines=feud_lines,
            sunday_extra=sunday_extra,
        )
        if grow_msg:
            digest += f"\n📜 {grow_msg}"
        if entity_digest_lines:
            digest += "\n" + "\n".join(entity_digest_lines)

        self._db.update_realm(realm_id, last_digest_text=digest)

        return {
            "realm_id": int(realm_id),
            "digest": digest,
            "chat_id": realm["chat_id"],
            "outcomes": outcomes,
        }

    def _prepare_tick_minor(
        self,
        realm_id: int,
        *,
        consume_pending: bool = True,
    ) -> str | None:
        """Берёт заранее свёрстанный минор (для слухов) или роллит заново.

        consume_pending=False: часы/ключ уже выставлены континентом - только эффекты.
        """
        realm = self._db.get_realm(realm_id)
        if not realm:
            return None

        tick_index = int(realm.get("tick_index") or 0)
        self._resolve_active_minor_events(realm_id)
        if consume_pending:
            pending_raw = realm.get("pending_minor_key")
            if pending_raw is None:
                minor_key = roll_minor_event(random.Random())
            else:
                minor_key = pending_raw or None
                self._db.update_realm(realm_id, pending_minor_key=None)
        else:
            minor_key = realm.get("active_minor_key") or None
        if not minor_key:
            if consume_pending:
                self._db.update_realm(
                    realm_id, active_minor_key=None, active_minor_until=None
                )
            return None
        if minor_key not in MINOR_EVENTS:
            if consume_pending:
                self._db.update_realm(
                    realm_id, active_minor_key=None, active_minor_until=None
                )
            return None

        meta = MINOR_EVENTS[minor_key]
        narrative = meta["canned_narrative"]
        duration_t = int(minor_effect(minor_key).get("duration_ticks") or 1)
        resolves_tick = tick_index + duration_t
        if consume_pending:
            self._db.update_realm(
                realm_id,
                active_minor_key=minor_key,
                active_minor_until=None,
            )
        event_line = event_digest_line(meta)
        self._apply_instant_minor(realm_id, minor_key)
        # Засуха остаётся active до следующего тика (farm_mult), без личного выкупа.
        status = "active" if minor_key == "drought" else "resolved"
        self._db.create_event(
            realm_id=realm_id,
            kind="minor",
            event_key=minor_key,
            payload={},
            narrative=narrative,
            status=status,
            resolves_tick=resolves_tick,
        )
        return event_line

    def _resolve_tile_entities(
        self, realm_id: int, tick_index: int
    ) -> tuple[list[str], tuple[ActiveTileEntityRef, ...]]:
        """Один SELECT на долину за тик; без строк - ([], ()) и digest не трогаем."""
        rows = self._db.list_active_tile_entities(realm_id)
        if not rows:
            return [], ()

        def update_entity(entity_id: int, **fields: Any) -> None:
            self._db.update_tile_entity(entity_id, **fields)
            for row in rows:
                if int(row["id"]) == int(entity_id):
                    row.update(fields)

        lines = resolve_realm_tile_entities(
            TileEntityResolveCtx(
                tick_index=tick_index,
                list_active=lambda: rows,
                expire_entity=self._db.claim_expire_tile_entity,
                update_entity=update_entity,
            )
        )
        surviving = tuple(
            active_tile_entity_ref(row)
            for row in rows
            if row.get("expires_tick") is None
            or int(row["expires_tick"]) > int(tick_index)
        )
        return lines, surviving

    def _resolve_active_minor_events(self, realm_id: int) -> None:
        for ev in self._db.get_active_events(realm_id, kind="minor"):
            self._db.update_event(ev["id"], status="resolved")

    def _apply_instant_minor(self, realm_id: int, key: str) -> None:
        apply_instant_minor(
            key,
            InstantMinorCtx(
                fiefs=list(self._db.list_fiefs(realm_id)),
                barn_level=self._engine.barn_level,
                fief_tiles=self._db.fief_tiles,
                update_fief=self._db.update_fief,
                update_tile=self._db.update_tile,
                rng=random,
            ),
        )

    def _feud_lines(self, realm_id: int) -> list[str]:
        realm = self._db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        since_tick = max(0, tick_index - B.FEUD_WINDOW_TICKS)
        raids = self._db.raids_since_tick(realm_id, since_tick)
        counts: dict[tuple[int, int], int] = {}
        for r in raids:
            key = (r["attacker_fief_id"], r["victim_fief_id"])
            counts[key] = counts.get(key, 0) + 1
        lines = []
        for (a, v), c in counts.items():
            if c >= B.FEUD_RAIDS_IN_WINDOW:
                af = self._db.get_fief(a)
                vf = self._db.get_fief(v)
                if af and vf:
                    lines.append(f"{self._engine.fief_label(af)} против {self._engine.fief_label(vf)}")
        return lines

    def _sunday_extra(self, realm_id: int) -> str:
        fiefs = self._db.list_fiefs(realm_id)
        if not fiefs:
            return ""
        by_tiles = sorted(
            fiefs,
            key=lambda f: len(self._db.fief_tiles(f["id"])),
            reverse=True,
        )
        top = by_tiles[0]
        return f"Титулы: больше всех земель - {self._engine.fief_label(top)}."

