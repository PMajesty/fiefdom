"""Катастрофы континента: волны, heal, resolve, вклад силы."""
from __future__ import annotations

from app.repos import CatastropheRepos

import logging
import math
import random
from dataclasses import dataclass

from app import balance as B
from app.domain.event_apply import CatastropheResolveCtx, resolve_catastrophe
from app.domain.events import (
    CATASTROPHES,
    next_catastrophe_delay_ticks,
    pick_catastrophe,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatastropheAnnounce:
    realm_id: int
    text: str
    event_id: int | None
    key: str


class CatastropheService:
    def __init__(self, engine, db: CatastropheRepos | None = None) -> None:
        self._engine = engine
        self._db = db if db is not None else engine.db

    def contribute_catastrophe_might(
        self, event_id: int, user_id: int, amount: int = 5
    ) -> int:
        """Вклад силы в активную катастрофу. Возвращает сумму в котле."""
        amt = int(amount)
        if amt <= 0:
            raise ValueError("Недостаточно силы")
        ev = self._db.get_event(event_id)
        if not ev or ev.get("status") != "active":
            raise ValueError("Событие уже завершено")
        fief = self._db.get_fief_by_user(ev["realm_id"], user_id)
        if not fief:
            raise ValueError("Сначала получите усадьбу в личке")
        self._engine._require_action_window(int(fief["realm_id"]))
        with self._db.transaction():
            ev = self._db.get_event(event_id)
            if not ev or ev.get("status") != "active":
                raise ValueError("Событие уже завершено")
            self._engine._require_action_window(int(fief["realm_id"]))
            if not self._db.debit_fief_resources(int(fief["id"]), might=amt):
                raise ValueError("Недостаточно силы")
            self._db.bump_event_action(event_id, int(fief["id"]), "might", amt)
            total = sum(
                int(a.get("amount") or 0) for a in self._db.event_actions(event_id)
            )
        return total

    def _active_catastrophe(self, realm_id: int) -> dict | None:
        events = self._db.get_active_events(realm_id, kind="catastrophe")
        return events[0] if events else None

    def _announce_text(
        self, realm: dict, key: str, narrative: str, window_t: int
    ) -> str:
        meta = CATASTROPHES[key]
        players = max(1, len(self._db.list_fiefs(realm["id"])))
        extra = ""
        if key == "bandit_night":
            need = int(math.ceil(B.BANDIT_NIGHT_MIGHT_PER_PLAYER * players))
            extra = (
                f"\nНужно собрать ≥ {need} силы. "
                f"Вклад: кнопка ниже (−5 силы за нажатие)."
            )
        elif key == "cattle_plague":
            extra = "\nПоля без тягла отдают едва ли треть, пока мор не отступит."
        return (
            f"⚠️ <b>{meta['name_ru']}</b>\n"
            f"{narrative}{extra}\n"
            f"Окно: {window_t} тик(а)."
        )

    def _advance_catastrophe_schedule(
        self, world: dict, key: str, tick_index: int
    ) -> None:
        rng = random.Random()
        delay = next_catastrophe_delay_ticks(rng)
        next_key = pick_catastrophe(rng, key)
        self._db.update_world(
            int(world["id"]),
            last_catastrophe_key=key,
            next_catastrophe_tick=tick_index + delay,
            next_catastrophe_key=next_key,
            next_catastrophe_at=None,
        )
        self._db.sync_realms_clock_from_world(int(world["id"]))

    @staticmethod
    def _wave_pair(event: dict) -> tuple[str, int]:
        return (str(event.get("event_key")), int(event.get("resolves_tick") or 0))

    def _pick_canonical_catastrophe_wave(
        self, active_pairs: list[tuple[dict, dict]], world: dict
    ) -> tuple[str, int]:
        """Каноническая волна при расхождении активных катастроф между долинами.

        Порядок: большинство; ключ по состоянию расписания; earliest resolves_tick;
        стабильный event_key.
        """
        counts: dict[tuple[str, int], int] = {}
        for _realm, ev in active_pairs:
            pair = self._wave_pair(ev)
            counts[pair] = counts.get(pair, 0) + 1
        max_count = max(counts.values())
        candidates = [pair for pair, n in counts.items() if n == max_count]
        if len(candidates) == 1:
            return candidates[0]

        tick_index = int(world.get("tick_index") or 0)
        next_tick = world.get("next_catastrophe_tick")
        schedule_due = next_tick is not None and tick_index >= int(next_tick)
        # Due: волна ещё из next_*; уже сдвинули - из last_* (текущая/прошлая волна).
        preferred_key = (
            world.get("next_catastrophe_key")
            if schedule_due
            else world.get("last_catastrophe_key")
        )
        if preferred_key is not None:
            keyed = [p for p in candidates if p[0] == str(preferred_key)]
            if keyed:
                candidates = keyed
                if len(candidates) == 1:
                    return candidates[0]

        min_resolves = min(p[1] for p in candidates)
        candidates = [p for p in candidates if p[1] == min_resolves]
        if len(candidates) == 1:
            return candidates[0]

        candidates.sort(key=lambda p: p[0])
        return candidates[0]

    def _heal_divergent_catastrophe_wave(
        self, world: dict, active_pairs: list[tuple[dict, dict]]
    ) -> tuple[str, int, set[int]]:
        """Сводит расходящиеся активные волны к одной; без игровых штрафов/лута.

        Возвращает (event_key, resolves_tick, realm_ids с канонической волной).
        """
        key, resolves_tick = self._pick_canonical_catastrophe_wave(active_pairs, world)
        canonical = (key, resolves_tick)
        have_ids: set[int] = set()
        closed: list[int] = []
        for realm, ev in active_pairs:
            rid = int(realm["id"])
            if self._wave_pair(ev) == canonical:
                have_ids.add(rid)
                continue
            # Sync heal: закрываем без resolve_catastrophe и прочих gameplay-эффектов.
            self._db.update_event(int(ev["id"]), status="resolved")
            closed.append(rid)
        logger.warning(
            "catastrophe wave divergence healed world=%s canonical=%s resolves_tick=%s "
            "kept_realms=%s closed_realms=%s",
            world.get("id"),
            key,
            resolves_tick,
            sorted(have_ids),
            sorted(closed),
        )
        return key, resolves_tick, have_ids

    def plan_world_catastrophe(self, world: dict) -> list[CatastropheAnnounce]:
        """Глобальная катастрофа: события в БД и сдвиг расписания; посты - снаружи.

        Неполный fan-out дополняется на следующем опросе без второго цикла бед.
        Расхождение ключей активной волны лечится до resume, а не стопорит fan-out навсегда.
        """
        tick_index = int(world.get("tick_index") or 0)
        next_tick = world.get("next_catastrophe_tick")
        realms = self._db.list_realms_by_chain(int(world["id"]))
        if not realms:
            return []

        active_pairs: list[tuple[dict, dict]] = []
        for realm in realms:
            ev = self._active_catastrophe(int(realm["id"]))
            if ev:
                active_pairs.append((realm, ev))

        announces: list[CatastropheAnnounce] = []

        if active_pairs:
            wave_keys = {self._wave_pair(ev) for _r, ev in active_pairs}
            if len(wave_keys) != 1:
                key, resolves_tick, have_ids = self._heal_divergent_catastrophe_wave(
                    world, active_pairs
                )
            else:
                key, resolves_tick = next(iter(wave_keys))
                have_ids = {int(r["id"]) for r, _ev in active_pairs}
            meta = CATASTROPHES.get(key) or {}
            narrative = meta.get("canned_narrative") or ""
            wave_expired = resolves_tick <= tick_index
            window_t = (
                max(1, resolves_tick - tick_index) if resolves_tick >= tick_index else 1
            )
            for realm in realms:
                if int(realm["id"]) in have_ids:
                    continue
                payload: dict = {"threshold_hint": True}
                if wave_expired:
                    # Sync-placeholder: не открываем active, который сразу получит fail-штрафы.
                    self._db.create_event(
                        realm_id=realm["id"],
                        kind="catastrophe",
                        event_key=key,
                        payload=payload,
                        narrative=narrative,
                        status="resolved",
                        resolves_tick=resolves_tick,
                    )
                    continue
                event = self._db.create_event(
                    realm_id=realm["id"],
                    kind="catastrophe",
                    event_key=key,
                    payload=payload,
                    narrative=narrative,
                    status="active",
                    resolves_tick=resolves_tick,
                )
                try:
                    text = self._announce_text(realm, key, narrative, window_t)
                    announces.append(
                        CatastropheAnnounce(
                            realm_id=int(realm["id"]),
                            text=text,
                            event_id=int(event["id"]),
                            key=key,
                        )
                    )
                except Exception:
                    logger.exception(
                        "catastrophe resume announce failed realm=%s",
                        realm.get("id"),
                    )
            # Если волна началась, а расписание ещё не сдвинули (упали до advance).
            if next_tick is not None and tick_index >= int(next_tick):
                self._advance_catastrophe_schedule(world, key, tick_index)
            return announces

        if next_tick is None or tick_index < int(next_tick):
            return []

        rng = random.Random()
        key = world.get("next_catastrophe_key") or pick_catastrophe(
            rng, world.get("last_catastrophe_key")
        )
        meta = CATASTROPHES[key]
        window_t = rng.randint(
            B.CATASTROPHE_WINDOW_TICKS_MIN, B.CATASTROPHE_WINDOW_TICKS_MAX
        )
        resolves_tick = tick_index + window_t
        narrative = meta["canned_narrative"]

        created: list[tuple[dict, dict]] = []
        for realm in realms:
            payload: dict = {"threshold_hint": True}
            event = self._db.create_event(
                realm_id=realm["id"],
                kind="catastrophe",
                event_key=key,
                payload=payload,
                narrative=narrative,
                status="active",
                resolves_tick=resolves_tick,
            )
            created.append((realm, event))

        # Сдвиг до Telegram-постов: повторный due не откроет вторую волну.
        self._advance_catastrophe_schedule(world, key, tick_index)

        for realm, event in created:
            try:
                text = self._announce_text(realm, key, narrative, window_t)
                announces.append(
                    CatastropheAnnounce(
                        realm_id=int(realm["id"]),
                        text=text,
                        event_id=int(event["id"]),
                        key=key,
                    )
                )
            except Exception:
                logger.exception(
                    "catastrophe announce failed realm=%s", realm.get("id")
                )
        return announces

    def iter_expired_resolutions(self, realm: dict):
        """Разрешить просроченные катастрофы по одной; yield текста для публичного поста.

        Resolve и пост снаружи должны чередоваться: сбой поста не закрывает
        следующие события заранее.
        """
        tick_index = int(realm.get("tick_index") or 0)
        events = self._db.get_active_events(realm["id"], kind="catastrophe")
        for ev in events:
            resolves_tick = ev.get("resolves_tick")
            if resolves_tick is None:
                continue
            if int(resolves_tick) > tick_index:
                continue

            key = str(ev.get("event_key") or "")
            result_text = resolve_catastrophe(
                key,
                CatastropheResolveCtx(
                    event_id=int(ev["id"]),
                    fiefs=list(self._db.list_fiefs(realm["id"])),
                    event_actions=list(self._db.event_actions(ev["id"])),
                    get_fief=self._db.get_fief,
                    update_fief=self._db.update_fief,
                    update_event=self._db.update_event,
                ),
            )
            if result_text:
                yield result_text
