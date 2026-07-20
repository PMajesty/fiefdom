"""Жизненный цикл долины: основание и вайп континента."""
from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import balance as B
from app.balance import best_rectangle
from app.config import TICK_HOUR, TICK_MINUTE, TIMEZONE, tick_slots
from app.domain.events import next_catastrophe_delay_ticks, pick_catastrophe
from app.domain.map_gen import generate_map
from app.domain.portals import pick_portal_insertion
from app.domain.realm_identity import CLOCK_MODE_SHARED, REALM_KIND_VALLEY
from app.domain.tick_schedule import format_tick_slots, schedule_anchor_at
from app.engine import _utcnow

logger = logging.getLogger(__name__)


class RealmLifecycleService:
    def __init__(self, engine) -> None:
        self._engine = engine
        self._db = engine.db

    def world_id_for_realm(self, realm_id: int) -> int:
        realm = self._db.get_realm(realm_id) or {}
        wid = realm.get("world_id")
        if wid is not None:
            return int(wid)
        return int(self._db.get_or_create_world()["id"])

    def create_realm(self, chat_id: int, title: str, creator_user_id: int) -> tuple[dict, str]:
        existing = self._db.get_realm_by_chat(chat_id)
        if existing:
            raise ValueError("В этом чате долина уже основана. Используйте /вч_карта")

        width, height = best_rectangle(B.MAP_MIN_TILES)
        tiles = generate_map(width, height)
        world = self._db.get_or_create_world()
        world_id = int(world["id"])
        tz = world.get("timezone") or TIMEZONE
        rng = random.Random()
        slots = tick_slots()
        existing_realms = self._db.list_realms_by_chain(world_id)

        if not existing_realms:
            delay = next_catastrophe_delay_ticks(rng)
            first_cat = pick_catastrophe(rng, None)
            local_now = datetime.now(ZoneInfo(tz))
            # Только уже прошедшие слоты; будущие слоты дня не сжигаем.
            anchor_date, anchor_slot = schedule_anchor_at(
                local_now=local_now, slots=slots
            )
            self._db.update_world(
                world_id,
                timezone=tz,
                next_catastrophe_tick=delay,
                next_catastrophe_key=first_cat,
                last_tick_local_date=anchor_date,
                last_tick_slot=anchor_slot,
            )
            world = self._db.get_world(world_id) or world
            chain_index = 0
            neighbor_note = ""
        else:
            indices = [int(r["chain_index"]) for r in existing_realms]
            anchor_idx, _side, new_index = pick_portal_insertion(indices, rng)
            chain_index = new_index
            anchor = next(
                (r for r in existing_realms if int(r["chain_index"]) == anchor_idx),
                existing_realms[0],
            )
            neighbor_note = (
                f"\nДолина на общем континенте с <b>{anchor['title']}</b> "
                f"и остальными долинами мира."
            )

        world = self._db.get_world(world_id) or world
        world_tick = int(world.get("tick_index") or 0)
        try:
            with self._db.transaction():
                if existing_realms:
                    self._db.shift_chain_indices(world_id, chain_index, delta=1)
                realm = self._db.create_realm(
                    chat_id=chat_id,
                    title=title or "Долина",
                    width=width,
                    height=height,
                    timezone=tz,
                    tick_hour=TICK_HOUR,
                    tick_minute=TICK_MINUTE,
                    feature_flags=dict(B.DEFAULT_FEATURE_FLAGS),
                    next_catastrophe_tick=world.get("next_catastrophe_tick"),
                    world_id=world_id,
                    chain_index=chain_index,
                    day_number=int(world.get("day_number") or 1),
                    tick_index=world_tick,
                    last_tick_local_date=world.get("last_tick_local_date"),
                    last_tick_slot=world.get("last_tick_slot"),
                    next_catastrophe_key=world.get("next_catastrophe_key"),
                    pending_minor_key=world.get("pending_minor_key"),
                    active_minor_key=world.get("active_minor_key"),
                    clock_mode=CLOCK_MODE_SHARED,
                    realm_kind=REALM_KIND_VALLEY,
                )
                self._db.update_realm(int(realm["id"]), last_economy_tick=world_tick)
                self._db.insert_tiles(
                    realm["id"],
                    [
                        {
                            "x": t.x,
                            "y": t.y,
                            "tile_type": t.tile_type,
                            "is_bridge": t.is_bridge,
                        }
                        for t in tiles
                    ],
                )
        except Exception:
            if existing_realms:
                try:
                    self._db.recompact_chain_indices(world_id)
                except Exception:
                    logger.exception(
                        "recompact_chain_indices failed after portal insert error"
                    )
            raise
        realm = self._db.get_realm(realm["id"]) or realm
        msg = (
            f"🏰 Вотчина основана: <b>{realm['title']}</b>\n"
            f"Карта {width}×{height}. День континента {realm['day_number']}. "
            f"Тики каждый день в {format_tick_slots(slots)} ({tz})."
            f"{neighbor_note}\n"
            f"Напишите боту в личку или нажмите \"Моё владение\", чтобы получить усадьбу."
        )
        return realm, msg

    def begin_wipe(self, realm_id: int) -> str:
        """Старт вайпа континента (все долины мира этой долины)."""
        realm = self._db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        world_id = self._engine._world_id_for_realm(realm_id)
        code = secrets.token_hex(3).upper()
        self._db.update_world(
            world_id,
            wipe_confirm_code=code,
            wipe_confirm_until=_utcnow() + timedelta(minutes=10),
        )
        n = len(self._db.list_realms_by_chain(world_id))
        return (
            f"⚠️ Удаление <b>всего континента</b> ({n} долин), якорь id={realm_id}.\n"
            f"Чтобы подтвердить, отправьте:\n"
            f"<code>/вч_wipe {realm_id} {code} УДАЛИТЬ</code>\n"
            f"Код действует 10 минут. Отдельная долина не стирается - только весь мир."
        )

    def confirm_wipe(self, realm_id: int, code: str, confirm_word: str) -> str:
        realm = self._db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        if confirm_word != "УДАЛИТЬ":
            raise ValueError("Нужно слово УДАЛИТЬ")
        world_id = self._engine._world_id_for_realm(realm_id)
        world = self._db.get_world(world_id) or {}
        until = world.get("wipe_confirm_until")
        if not world.get("wipe_confirm_code") or not until or until < _utcnow():
            raise ValueError("Нет активного кода. Сначала /вч_wipe_start")
        if code.upper() != str(world["wipe_confirm_code"]).upper():
            raise ValueError("Неверный код")
        realms = self._db.list_realms_by_chain(world_id)
        for r in realms:
            self._db.delete_realm(int(r["id"]))
        self._db.update_world(
            world_id,
            wipe_confirm_code=None,
            wipe_confirm_until=None,
            day_number=1,
            tick_index=0,
            forced_tick_count=0,
            active_minor_key=None,
            pending_minor_key=None,
            next_catastrophe_tick=None,
            next_catastrophe_key=None,
            last_catastrophe_key=None,
            last_tick_at=None,
            last_tick_local_date=None,
            last_tick_slot=None,
        )
        return f"Континент стёрт ({len(realms)} долин). Можно снова /вотчина."

    def list_realms_with_fief_counts(
        self,
    ) -> tuple[list[dict], dict[int, int]]:
        realms = self._db.list_realms()
        fief_counts = {
            int(r["id"]): len(self._db.list_fiefs(int(r["id"]))) for r in realms
        }
        return realms, fief_counts

    def get_realm(self, realm_id: int) -> dict | None:
        return self._db.get_realm(realm_id)

    def grant_resources(
        self,
        realm_id: int,
        fief_id: int,
        deltas: dict[str, int],
    ) -> None:
        fief = self._db.get_fief(fief_id)
        if not fief or fief["realm_id"] != realm_id:
            raise ValueError("Усадьба не найдена в этой долине")
        self._db.update_fief(
            fief_id,
            **{key: int(fief[key]) + int(deltas[key]) for key in deltas},
        )

    def set_fief_frozen(self, fief_id: int, frozen: bool) -> None:
        fief = self._db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        self._db.update_fief(fief_id, frozen=bool(frozen))

    def set_active_minor(self, realm_id: int, key: str) -> None:
        realm = self._db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        world_id = self._engine._world_id_for_realm(realm_id)
        self._db.update_world(
            world_id,
            active_minor_key=key,
            active_minor_until=None,
        )
        self._db.sync_realms_clock_from_world(world_id)

    def issue_decree(self, realm_id: int, body: str) -> int:
        realm = self._db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        number = self._db.next_decree_number(realm_id)
        self._db.add_decree(realm_id, number, body)
        return number
