"""Слухи: план очереди, due-ролл, архив catch-up."""
from __future__ import annotations

from app.repos import RumorRepos

import random
from datetime import datetime
from typing import Any

from app import balance as B
from app.domain.rumors import (
    FiefRumorSnapshot,
    UpcomingEventHint,
    append_rumor_archive,
    format_rumors_pull,
    in_rumor_quiet_hours,
    parse_rumor_queue,
    parse_stored_rumors,
    plan_rumor_due_times,
    rumor_count_for_window,
    rumor_queue_storage,
    roll_rumor_line,
)
from app.domain.tick_pipeline import TICK_PHASE_PLAY, normalize_tick_phase
from app.domain.ticks import tick_active


class RumorService:
    def __init__(self, engine, db: RumorRepos) -> None:
        self._engine = engine
        self._db = db

    def _rumor_snapshots(
        self,
        realm_id: int,
        *,
        realm_title: str | None = None,
    ) -> list[FiefRumorSnapshot]:
        realm = self._db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        title = (
            str(realm_title)
            if realm_title is not None
            else str(realm.get("title") or "")
        )
        out: list[FiefRumorSnapshot] = []
        for fief in self._db.list_fiefs(realm_id):
            if fief.get("frozen"):
                continue
            buildings = tuple(
                (str(t["building"]), int(t["building_level"]))
                for t in self._db.fief_tiles(fief["id"])
                if t.get("building")
                and int(t.get("building_level") or 0) > 0
                and not t.get("is_overgrown")
            )
            out.append(
                FiefRumorSnapshot(
                    fief_id=int(fief["id"]),
                    name=self._engine.fief_label(fief),
                    grain=int(fief["grain"]),
                    goods=int(fief["goods"]),
                    might=int(fief["might"]),
                    buildings=buildings,
                    patrol_active=tick_active(fief.get("patrol_until_tick"), tick_index),
                    realm_title=title,
                )
            )
        return out

    def _foreign_rumor_snapshots(self, realm_id: int) -> list[FiefRumorSnapshot]:
        """Усадьбы других долин того же континента (для чужих сплетен)."""
        out: list[FiefRumorSnapshot] = []
        for nb in self._db.list_adjacent_realms(realm_id):
            title = str(nb.get("title") or "долина")
            out.extend(
                self._engine._rumor_snapshots(int(nb["id"]), realm_title=title)
            )
        return out

    def _roll_rumor_line_for_realm(self, realm_id: int) -> str | None:
        return roll_rumor_line(
            self._engine._rumor_snapshots(realm_id),
            self._engine._foreign_rumor_snapshots(realm_id),
            self._engine._upcoming_event_hints(realm_id),
            random.Random(),
        )

    def _upcoming_event_hints(self, realm_id: int) -> list[UpcomingEventHint]:
        realm = self._db.get_realm(realm_id) or {}
        hints: list[UpcomingEventHint] = []
        pending = realm.get("pending_minor_key")
        if pending:
            hints.append(UpcomingEventHint(kind="minor", key=str(pending)))
        next_tick = realm.get("next_catastrophe_tick")
        next_key = realm.get("next_catastrophe_key")
        tick_index = int(realm.get("tick_index") or 0)
        if (
            next_tick is not None
            and next_key
            and int(next_tick) - tick_index <= B.RUMOR_CATASTROPHE_WARN_TICKS
            and int(next_tick) > tick_index
        ):
            hints.append(UpcomingEventHint(kind="catastrophe", key=str(next_key)))
        return hints

    def _same_play_opened_mark(self, left: Any, right: Any) -> bool:
        if left is None or right is None:
            return False
        a = left if isinstance(left, datetime) else None
        b = right if isinstance(right, datetime) else None
        if a is None:
            try:
                a = datetime.fromisoformat(str(left))
            except ValueError:
                return False
        if b is None:
            try:
                b = datetime.fromisoformat(str(right))
            except ValueError:
                return False
        aa = self._engine._as_aware_utc(a)
        bb = self._engine._as_aware_utc(b)
        if aa is None or bb is None:
            return False
        return aa.replace(microsecond=0) == bb.replace(microsecond=0)

    def plan_world_rumor_queues(self, world_id: int) -> None:
        """После входа в play: 1-2 due на окно. Без окна - очистить stale."""
        world = self._db.get_world(int(world_id)) or {}
        bounds = self._engine.play_window_bounds_for_world(world)
        world = self._db.get_world(int(world_id)) or world
        realms = self._db.list_realms_by_chain(int(world_id))
        opened = world.get("play_opened_at")
        if bounds is None:
            for realm in realms:
                self._db.update_realm(int(realm["id"]), rumor_queue=[])
            self._db.update_world(
                int(world_id), rumor_plan_play_opened_at=opened
            )
            return
        window_start, window_end = bounds
        rng = random.Random()
        for realm in realms:
            count = rumor_count_for_window(rng)
            dues = plan_rumor_due_times(
                window_start, window_end, count, rng=rng
            )
            self._db.update_realm(
                int(realm["id"]),
                rumor_queue=rumor_queue_storage(dues),
            )
        self._db.update_world(int(world_id), rumor_plan_play_opened_at=opened)

    def ensure_rumor_queues_planned(self, world_id: int) -> None:
        """Деплой mid-play / crash: план один раз на текущий play_opened_at."""
        world = self._db.get_world(int(world_id)) or {}
        if normalize_tick_phase(world.get("tick_phase")) != TICK_PHASE_PLAY:
            return
        if self._engine.world_tick_incomplete(int(world_id)):
            return
        if world.get("play_opened_at") is None:
            return
        if self._engine._same_play_opened_mark(
            world.get("rumor_plan_play_opened_at"),
            world.get("play_opened_at"),
        ):
            return
        self._engine.plan_world_rumor_queues(int(world_id))

    def maybe_due_rumors(
        self, world_id: int, local_now: datetime
    ) -> list[dict[str, Any]]:
        """Due-слоты к публикации. Очередь чистится только после успешного поста."""
        if in_rumor_quiet_hours(local_now):
            return []
        out: list[dict[str, Any]] = []
        for realm in self._db.list_realms_by_chain(int(world_id)):
            rid = int(realm["id"])
            raw_queue = realm.get("rumor_queue") or []
            if not isinstance(raw_queue, list):
                continue
            for item in raw_queue:
                key = item.get("due") if isinstance(item, dict) else item
                if key is None:
                    continue
                due_key = str(key)
                try:
                    due_local = datetime.fromisoformat(due_key)
                except ValueError:
                    continue
                if due_local.tzinfo is None and local_now.tzinfo is not None:
                    due_local = due_local.replace(tzinfo=local_now.tzinfo)
                if due_local > local_now:
                    continue
                text = self._engine._roll_rumor_line_for_realm(rid)
                out.append(
                    {
                        "realm_id": rid,
                        "due": due_key,
                        "text": text,
                    }
                )
        return out

    def acknowledge_rumor_posted(
        self,
        realm_id: int,
        due_iso: str,
        text: str | None,
    ) -> None:
        """Снять due из очереди и дописать строку в архив catch-up."""
        realm = self._db.get_realm(int(realm_id))
        if not realm:
            return
        target = str(due_iso)
        try:
            target_dt = datetime.fromisoformat(target)
        except ValueError:
            return
        raw_queue = realm.get("rumor_queue") or []
        if not isinstance(raw_queue, list):
            return
        kept_raw: list[Any] = []
        removed = False
        for item in raw_queue:
            key = item
            if isinstance(item, dict):
                key = item.get("due")
            key_s = str(key) if key is not None else ""
            same = key_s == target
            if not same and key_s:
                try:
                    item_dt = datetime.fromisoformat(key_s)
                    left = item_dt
                    right = target_dt
                    if left.tzinfo is None and right.tzinfo is not None:
                        left = left.replace(tzinfo=right.tzinfo)
                    if right.tzinfo is None and left.tzinfo is not None:
                        right = right.replace(tzinfo=left.tzinfo)
                    same = left == right
                except ValueError:
                    same = False
            if not removed and same:
                removed = True
                continue
            kept_raw.append(item)
        if not removed:
            return
        archive = parse_stored_rumors(realm.get("last_rumor_lines"))
        if text:
            archive = append_rumor_archive(archive, text)
        kept = parse_rumor_queue(kept_raw)
        self._db.update_realm(
            int(realm_id),
            rumor_queue=rumor_queue_storage(kept),
            last_rumor_lines=archive,
        )

    def rumors_text(self, realm_id: int) -> str:
        realm = self._db.get_realm(realm_id)
        if not realm:
            return format_rumors_pull([])
        return format_rumors_pull(parse_stored_rumors(realm.get("last_rumor_lines")))
