"""Оркестрация тика континента: ночь -> часы -> economy -> play."""
from __future__ import annotations

from app.repos import WorldTickRepos

import logging
import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import TIMEZONE, tick_slots
from app.domain.events import roll_minor_event
from app.domain.raids import ResolveNightReport
from app.domain.tick_pipeline import (
    TICK_PHASE_ECONOMY,
    TICK_PHASE_PLAY,
    TICK_PHASE_RESOLVE,
    TickPipeline,
    normalize_tick_phase,
)
from app.engine import _as_date, _utcnow

logger = logging.getLogger(__name__)


class WorldTickOrchestrator:
    def __init__(self, engine, db: WorldTickRepos | None = None) -> None:
        self._engine = engine
        self._db = db if db is not None else engine.db

    def _enter_tick_economy(
        self, world_id: int, world: dict | None = None
    ) -> None:
        if (
            world is not None
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY
        ):
            return
        self._db.update_world(int(world_id), **TickPipeline.economy_fields())
        if world is not None:
            world["tick_phase"] = TICK_PHASE_ECONOMY

    def _enter_tick_resolve(
        self,
        world_id: int,
        resolve_tick_index: int,
        world: dict | None = None,
    ) -> None:
        fields = {
            **TickPipeline.resolve_fields(),
            "resolve_tick_index": int(resolve_tick_index),
        }
        self._db.update_world(int(world_id), **fields)
        if world is not None:
            world.update(fields)

    def _enter_tick_play(
        self,
        world_id: int,
        world: dict | None = None,
        **extra: Any,
    ) -> None:
        if (
            world is not None
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_PLAY
            and not extra
            and world.get("play_opened_at") is not None
        ):
            return
        fields = {
            **TickPipeline.play_fields(),
            "play_opened_at": _utcnow(),
            "resolve_tick_index": None,
            **extra,
        }
        self._db.update_world(int(world_id), **fields)
        if world is not None:
            world.update(fields)

    def _close_play_day_raids(
        self, world_id: int, tick_index: int, world: dict
    ) -> ResolveNightReport:
        """Force-lock набегов + ночной resolve (набеги, затем обозы)."""
        if self._engine.world_tick_incomplete(int(world_id)):
            return ResolveNightReport()
        self._enter_tick_resolve(int(world_id), int(tick_index), world)
        self._engine.lock_open_raid_intents(int(world_id))
        report = self._engine.resolve_pending_raids(int(world_id), int(tick_index))
        caravan_report = self._engine.resolve_pending_caravans(
            int(world_id), int(tick_index)
        )
        report.notices.extend(caravan_report.notices)
        for realm_id, line in caravan_report.digest_lines:
            self._engine._append_pending_raid_line(int(realm_id), line)
        self._db.update_world(int(world_id), resolve_tick_index=None)
        if world is not None:
            world["resolve_tick_index"] = None
        return report

    def run_world_tick(
        self,
        world_id: int | None = None,
        tick_slot: int | None = None,
    ) -> dict:
        """Один тик континента: ночной resolve → часы → economy → play.

        Часы двигаются один раз; экономика каждой долины идемпотентна по
        last_economy_tick. При обрыве следующий вызов догоняет отстающие долины
        без повторного сдвига tick_index и календарного дня.
        """
        world = self._db.get_world(world_id) if world_id else self._db.get_or_create_world()
        if not world:
            raise ValueError("Континент не найден")
        wid = int(world["id"])
        realms = self._db.list_realms_by_chain(wid)
        if not realms:
            # Пустой континент не двигает часы; play чтобы не зависнуть в economy.
            self._enter_tick_play(wid, world)
            return {"world_id": wid, "realms": [], "digest": None, "chat_id": None}

        current = int(world.get("tick_index") or 0)
        # Легаси/новая колонка: NULL значит "уже на текущих часах", не "отстаёт".
        for r in realms:
            if r.get("last_economy_tick") is None:
                self._db.update_realm(int(r["id"]), last_economy_tick=current)
                r["last_economy_tick"] = current

        economies_done = all(
            int(r.get("last_economy_tick") or -1) >= current for r in realms
        )
        night_report = ResolveNightReport()

        # Crash mid-resolve: добить ночь до сдвига часов.
        if normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_RESOLVE:
            resolve_tick = int(
                world.get("resolve_tick_index")
                if world.get("resolve_tick_index") is not None
                else current
            )
            night_report = self._close_play_day_raids(wid, resolve_tick, world)
            world = self._db.get_world(wid) or world
            # Дальше - обычный advance с текущего tick_index (ещё не сдвинут).
            current = int(world.get("tick_index") or 0)
            realms = self._db.list_realms_by_chain(wid)
            economies_done = all(
                int(r.get("last_economy_tick") or -1) >= current for r in realms
            )

        # Crash после fan-out, до enter_play: закрыть окно без нового тика.
        if (
            current > 0
            and economies_done
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY
        ):
            play_fields: dict[str, Any] = {}
            if world.get("pending_minor_key") is None:
                play_fields["pending_minor_key"] = (
                    roll_minor_event(random.Random()) or ""
                )
            self._enter_tick_play(wid, world, **play_fields)
            self._db.sync_realms_clock_from_world(wid)
            self._engine.plan_world_rumor_queues(wid)
            return {
                "world_id": wid,
                "realms": [],
                "digest": None,
                "chat_id": None,
                "resumed": True,
                "incomplete": False,
                "raid_notices": list(night_report.notices),
            }

        resuming = any(
            int(r.get("last_economy_tick") or -1) < current for r in realms
        ) and current > 0

        if resuming:
            new_tick = current
            self._enter_tick_economy(wid, world)
        else:
            # Закрываем play-день T ночным resolve, затем двигаем часы на T+1.
            if (
                current > 0
                and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_PLAY
            ):
                night_report = self._close_play_day_raids(wid, current, world)
                world = self._db.get_world(wid) or world
            new_tick = current + 1
            pending_raw = world.get("pending_minor_key")
            if pending_raw is None:
                minor_key = roll_minor_event(random.Random())
            else:
                minor_key = pending_raw or None

            tz = ZoneInfo(world.get("timezone") or TIMEZONE)
            local_now = datetime.now(tz)
            local_date = local_now.date()
            day = int(world.get("day_number") or 1)
            world_fields: dict[str, Any] = {
                "tick_index": new_tick,
                "day_number": day,
                "last_tick_at": _utcnow(),
                "active_minor_key": minor_key,
                "active_minor_until": None,
                "pending_minor_key": None,
                "resolve_tick_index": None,
                **TickPipeline.economy_fields(),
            }
            # Плановые слоты двигает только scheduler (когда передан tick_slot).
            # Админский тик без слота: tick_index двигаем, календарный день и слоты - нет.
            if tick_slot is not None:
                slots = tick_slots()
                tick_slot = max(0, min(int(tick_slot), max(0, len(slots) - 1)))
                prev_local = _as_date(world.get("last_tick_local_date"))
                # Календарный день: +1 только когда курсор last_tick_local_date
                # переходит на новую локальную дату (не на каждый слот и не при NULL).
                if prev_local is not None and local_date > prev_local:
                    world_fields["day_number"] = day + 1
                world_fields["last_tick_local_date"] = local_date
                world_fields["last_tick_slot"] = tick_slot
            # Часы мира + зеркала долин - один COMMIT.
            # Иначе crash между update_world и sync оставляет economy на stale realm clock.
            with self._db.transaction():
                self._db.update_world(wid, **world_fields)
                self._db.sync_realms_clock_from_world(wid)
            world.update(world_fields)

        realm_results = []
        for realm in self._db.list_realms_by_chain(wid):
            rid = int(realm["id"])
            if int(realm.get("last_economy_tick") or -1) >= new_tick:
                realm_results.append(
                    {
                        "realm_id": rid,
                        "skipped": True,
                        "already_ticked": True,
                        "digest": None,
                        "chat_id": realm.get("chat_id"),
                    }
                )
                continue
            try:
                with self._db.transaction():
                    result = self._engine.run_realm_tick(
                        rid,
                        tick_slot=tick_slot,
                        advance_clock=False,
                    )
                    self._db.update_realm(rid, last_economy_tick=new_tick)
                realm_results.append(result)
            except Exception:
                logger.exception("realm tick failed world=%s realm=%s", wid, rid)
                realm_results.append(
                    {
                        "realm_id": rid,
                        "skipped": True,
                        "error": True,
                        "digest": None,
                        "chat_id": realm.get("chat_id"),
                    }
                )

        caught_up = all(
            int(r.get("last_economy_tick") or -1) >= new_tick
            for r in self._db.list_realms_by_chain(wid)
        )
        if caught_up:
            world = self._db.get_world(wid) or world
            play_fields: dict[str, Any] = {}
            if world.get("pending_minor_key") is None:
                play_fields["pending_minor_key"] = (
                    roll_minor_event(random.Random()) or ""
                )
            self._enter_tick_play(wid, world, **play_fields)
            self._db.sync_realms_clock_from_world(wid)
            self._engine.plan_world_rumor_queues(wid)

        posted = [x for x in realm_results if not x.get("skipped")]
        head = posted[0] if posted else (realm_results[0] if realm_results else {})
        return {
            "world_id": wid,
            "realms": realm_results,
            "digest": head.get("digest"),
            "chat_id": head.get("chat_id"),
            "resumed": resuming,
            "incomplete": not caught_up,
            "raid_notices": list(night_report.notices),
        }
