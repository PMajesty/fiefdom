"""Объявление и отмена набегов, midpoint lock."""
from __future__ import annotations

from app.repos import RaidDeclareRepos

from app import balance as B
from app.domain.raids import DeclareRaidResult
from app.domain.tick_pipeline import TICK_PHASE_PLAY, normalize_tick_phase
from app.domain.tick_schedule import (
    raid_declare_midpoint,
    raid_declare_open,
    raid_lock_due,
)
from app.domain.ticks import tick_active
from app.engine import _utcnow, raid_pact_lock_message, raid_pact_unlocked


class RaidDeclareService:
    def __init__(self, engine, db: RaidDeclareRepos) -> None:
        self._engine = engine
        self._db = db

    def list_raid_target_fiefs(self, attacker_fief_id: int) -> list[dict]:
        """Цели на всём континенте (без своей user_id)."""
        atk = self._db.get_fief(attacker_fief_id)
        if not atk:
            return []
        atk_uid = int(atk["user_id"])
        atk_realm = int(atk["realm_id"])
        realm_ids = {atk_realm}
        for nb in self._db.list_adjacent_realms(atk_realm):
            realm_ids.add(int(nb["id"]))
        out: list[dict] = []
        for rid in sorted(realm_ids):
            for f in self._db.list_fiefs(rid):
                if f.get("frozen"):
                    continue
                if int(f["id"]) == int(attacker_fief_id):
                    continue
                if int(f["user_id"]) == atk_uid:
                    continue
                if (
                    atk.get("pact_id")
                    and f.get("pact_id")
                    and int(atk["pact_id"]) == int(f["pact_id"])
                ):
                    continue
                item = dict(f)
                item["via_portal"] = int(f["realm_id"]) != atk_realm
                out.append(item)
        return out

    def raid_declare_is_open(self, world: dict) -> bool:
        local_now = self._engine._world_local_now(world)
        return raid_declare_open(local_now, self._engine.play_window_bounds_for_world(world))

    def format_raid_deadline(
        self, world: dict, *, midpoint: bool
    ) -> str:
        bounds = self._engine.play_window_bounds_for_world(world)
        if bounds is None:
            return "-"
        point = raid_declare_midpoint(bounds) if midpoint else bounds[1]
        return point.strftime("%d.%m %H:%M")

    def _refund_action(self, fief_id: int) -> None:
        fief = self._db.get_fief(fief_id)
        if not fief:
            return
        new_actions = min(B.ACTIONS_BANK_MAX, int(fief["actions"]) + 1)
        self._db.update_fief(int(fief_id), actions=new_actions)

    def _raid_declare_gates(
        self, attacker_id: int, victim_id: int, might: int
    ) -> tuple[dict, dict, dict, dict, int]:
        if might < B.RAID_MIN_MIGHT:
            raise ValueError(f"Минимум {B.RAID_MIN_MIGHT} силы")
        atk = self._engine.require_active_fief(attacker_id)
        vic = self._db.get_fief(victim_id)
        if not atk or not vic:
            raise ValueError("Цель не найдена")
        if not self._db.realms_are_adjacent(
            int(atk["realm_id"]), int(vic["realm_id"])
        ):
            raise ValueError("Цель не найдена")
        self._engine._require_cross_valley_caught_up(
            int(atk["realm_id"]), int(vic["realm_id"])
        )
        if atk["id"] == vic["id"]:
            raise ValueError("Нельзя грабить себя")
        if int(atk["user_id"]) == int(vic["user_id"]):
            raise ValueError("Нельзя грабить свою усадьбу")
        if (
            atk.get("pact_id")
            and vic.get("pact_id")
            and int(atk["pact_id"]) == int(vic["pact_id"])
        ):
            raise ValueError("Нельзя нападать на союзника по пакту")
        if atk["hungry"]:
            raise ValueError("Голодные мужики не воюют")
        if atk["might"] < might:
            raise ValueError("Недостаточно силы")
        realm = self._db.get_realm(atk["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        if tick_active(atk.get("shield_until_tick"), tick_index):
            raise ValueError("Пока действует щит, набеги недоступны")
        if tick_active(vic.get("shield_until_tick"), tick_index):
            raise ValueError("У жертвы щит после набега")
        last_pair = self._db.last_raid_attacker_victim(attacker_id, victim_id)
        last_reverse = self._db.last_raid_attacker_victim(victim_id, attacker_id)
        for raid_tick in (last_pair, last_reverse):
            if raid_tick is None:
                continue
            # Ночной лог на тике T должен закрывать пару на play-дне T+1.
            if int(raid_tick) + B.RAID_SAME_VICTIM_TICKS >= tick_index:
                raise ValueError("Кулдаун на эту пару усадеб")
        if not raid_pact_unlocked(
            onboard_step=int(atk.get("onboard_step") or 0),
            day_number=int(realm.get("day_number") or 1),
        ):
            raise ValueError(
                raid_pact_lock_message(
                    onboard_step=int(atk.get("onboard_step") or 0),
                    day_number=int(realm.get("day_number") or 1),
                )
            )
        wid = self._engine._world_id_for_realm(int(atk["realm_id"]))
        world = self._db.get_world(wid) or {}
        if not self._engine.raid_declare_is_open(world):
            raise ValueError(
                "Поздно объявлять набег: до закрытия заявок осталось меньше половины окна"
            )
        return atk, vic, realm, world, tick_index

    def declare_raid(
        self,
        attacker_id: int,
        victim_id: int,
        might: int,
        *,
        open_truce: bool = False,
    ) -> DeclareRaidResult:
        atk, vic, realm, world, tick_index = self._raid_declare_gates(
            attacker_id, victim_id, might
        )
        wid = int(world["id"])
        for intent in self._db.list_open_raid_intents_for_fief(attacker_id):
            if int(intent.get("tick_index") or -1) != tick_index:
                continue
            payload = intent.get("payload") or {}
            if int(payload.get("victim_id") or 0) == int(victim_id):
                raise ValueError("На эту цель уже есть заявка в этом тике")

        self._engine.collect_for_fief(attacker_id)
        atk = self._db.get_fief(attacker_id) or atk
        if int(atk["might"]) < might:
            raise ValueError("Недостаточно силы")

        same_realm = int(atk["realm_id"]) == int(vic["realm_id"])
        vic_realm = self._db.get_realm(vic["realm_id"]) or realm
        pact_hint = None
        truce = bool(open_truce)
        if atk.get("pact_id"):
            truce = False
            pact_hint = "Союзники по пакту сольются в один удар на дороге."
        elif truce:
            pact_hint = "Открытое перемирие: другие opt-in отряды сольются с вами."

        with self._db.transaction():
            self._engine._require_cross_valley_caught_up(
                int(atk["realm_id"]), int(vic["realm_id"])
            )
            if not self._engine.raid_declare_is_open(self._db.get_world(wid) or world):
                raise ValueError(
                    "Поздно объявлять набег: до закрытия заявок осталось меньше половины окна"
                )
            self._engine._spend_action(atk)
            if not self._db.debit_fief_resources(attacker_id, might=int(might)):
                raise ValueError("Недостаточно силы")
            intent = self._db.create_action_intent(
                world_id=wid,
                tick_index=tick_index,
                fief_id=attacker_id,
                kind="raid",
                status="open",
                payload={
                    "victim_id": int(victim_id),
                    "might": int(might),
                    "open_truce": truce,
                    "via_portal": not same_realm,
                    "attacker_realm_id": int(atk["realm_id"]),
                    "victim_realm_id": int(vic["realm_id"]),
                    "escrowed": True,
                    "attacker_pact_id": (
                        int(atk["pact_id"]) if atk.get("pact_id") else None
                    ),
                },
            )
            self._db.update_fief(
                attacker_id,
                last_raid_at=_utcnow(),
                last_raid_tick=tick_index,
            )

        atk_final = self._db.get_fief(attacker_id) or atk
        men_home = int(atk_final.get("might") or 0)
        lock_text = self._engine._format_raid_deadline(world, midpoint=True)
        resolve_text = self._engine._format_raid_deadline(world, midpoint=False)
        dm = (
            f"Дружина ушла в ночь на хутор {self._engine.fief_label(vic)}: "
            f"{might} силы в пути, дома {men_home}. "
            f"Заявку можно отменить до {lock_text}. "
            f"Бой в тик около {resolve_text}."
        )
        if pact_hint:
            dm = f"{dm} {pact_hint}"
        return DeclareRaidResult(
            intent_id=int(intent["id"]),
            victim_fief_id=int(victim_id),
            victim_name=self._engine.fief_label(vic),
            might=int(might),
            men_home=men_home,
            open_truce=truce,
            lock_deadline_text=lock_text,
            resolve_slot_text=resolve_text,
            pact_merge_hint=pact_hint,
            dm_text=dm,
        )

    def cancel_raid_intent(self, fief_id: int, intent_id: int) -> str:
        fief = self._engine.require_active_fief(fief_id)
        intent = self._db.get_action_intent(int(intent_id))

        if not intent or intent.get("kind") != "raid":
            raise ValueError("Заявка не найдена")
        if int(intent["fief_id"]) != int(fief_id):
            raise ValueError("Это не ваша заявка")
        if intent.get("status") != "open":
            raise ValueError("После закрытия заявок отменить нельзя")
        payload = dict(intent.get("payload") or {})
        might = int(payload.get("might") or 0)
        with self._db.transaction():
            claimed = self._db.cancel_action_intent(int(intent_id))
            if not claimed:
                raise ValueError("После закрытия заявок отменить нельзя")
            if might > 0:
                self._db.credit_fief_resources(fief_id, might=might)
            self._engine._refund_action(fief_id)
        return (
            f"Заявка снята: {might} силы и 1 действие вернулись "
            f"({self._engine.fief_label(fief)})."
        )

    def maybe_lock_raids_at_midpoint(self, world_id: int) -> int:
        """Scheduler: идемпотентный lock набегов и обозов после середины окна play."""
        world = self._engine.ensure_play_opened_at(int(world_id))
        if normalize_tick_phase(world.get("tick_phase")) != TICK_PHASE_PLAY:
            return 0
        if self._engine.world_tick_incomplete(int(world_id)):
            return 0
        local_now = self._engine._world_local_now(world)
        if not raid_lock_due(local_now, self._engine.play_window_bounds_for_world(world)):
            return 0
        return self._engine.lock_open_travel_intents(int(world_id))
