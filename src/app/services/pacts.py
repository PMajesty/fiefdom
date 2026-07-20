"""Пакты: создание, приглашения, выход, прикрытие."""
from __future__ import annotations

from app.repos import PactRepos

from app import balance as B
from app.domain.cover import COVER_MODE_ANY


class PactService:
    def __init__(self, engine, db: PactRepos) -> None:
        self._engine = engine
        self._db = db

    def get_pact(self, pact_id: int) -> dict | None:
        return self._db.get_pact(pact_id)

    def get_pact_invite(self, invite_id: int) -> dict | None:
        return self._db.get_pact_invite(invite_id)

    def create_pact(self, fief_id: int, name: str) -> str:
        fief = self._db.get_fief(fief_id)
        if fief.get("pact_id"):
            raise ValueError("Вы уже в пакте")
        self._engine._require_action_window(int(fief["realm_id"]))
        name = name.strip()[:40]
        if not name:
            raise ValueError("Нужно имя")
        pact = self._db.create_pact(fief["realm_id"], name, fief_id)
        return f"Пакт \"{pact['name']}\" создан. Приглашайте союзников."

    def invite_to_pact(self, founder_fief_id: int, target_fief_id: int) -> dict:
        """Создаёт открытое приглашение. Не меняет pact_id цели."""
        founder = self._db.get_fief(founder_fief_id)
        target = self._db.get_fief(target_fief_id)
        if not founder or not target:
            raise ValueError("Усадьба не найдена")
        if not founder.get("pact_id"):
            raise ValueError("Сначала создайте пакт")
        pact = self._db.get_pact(founder["pact_id"])
        if not pact or pact["founder_fief_id"] != founder_fief_id:
            raise ValueError("Приглашает только основатель")
        members = self._db.pact_members(pact["id"])
        if len(members) >= B.PACT_SIZE_MAX:
            raise ValueError("Пакт полон")
        if target.get("pact_id"):
            raise ValueError("Цель уже в пакте")
        if not self._db.realms_are_adjacent(
            int(founder["realm_id"]), int(target["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._engine._require_cross_valley_caught_up(
            int(founder["realm_id"]), int(target["realm_id"])
        )
        if founder_fief_id == target_fief_id:
            raise ValueError("Нельзя пригласить себя")
        if self._db.get_open_pact_invite(pact["id"], target_fief_id):
            raise ValueError("Приглашение уже отправлено")
        realm = self._db.get_realm(founder["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        with self._db.transaction():
            founder = self._db.get_fief(founder_fief_id)
            target = self._db.get_fief(target_fief_id)
            if not founder or not target:
                raise ValueError("Усадьба не найдена")
            self._engine._require_cross_valley_caught_up(
                int(founder["realm_id"]), int(target["realm_id"])
            )
            invite = self._db.create_pact_invite(
                realm_id=founder["realm_id"],
                pact_id=pact["id"],
                inviter_fief_id=founder_fief_id,
                target_fief_id=target_fief_id,
                expires_tick=tick_index + B.PACT_INVITE_EXPIRE_TICKS,
            )
        return invite

    def accept_pact_invite(self, target_fief_id: int, invite_id: int) -> str:
        invite = self._db.get_pact_invite(invite_id)
        if not invite or invite["status"] != "open":
            raise ValueError("Приглашение недоступно")
        realm = self._db.get_realm(invite["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        expires_tick = invite.get("expires_tick")
        if expires_tick is None or int(expires_tick) <= tick_index:
            self._db.update_pact_invite(invite_id, status="expired")
            raise ValueError("Приглашение истекло")
        if int(invite["target_fief_id"]) != int(target_fief_id):
            raise ValueError("Это приглашение не вам")
        target = self._db.get_fief(target_fief_id)
        if not target:
            raise ValueError("Усадьба не найдена")
        if target.get("pact_id"):
            raise ValueError("Вы уже в пакте")
        pact = self._db.get_pact(invite["pact_id"])
        if not pact:
            raise ValueError("Пакт распущен")
        if not self._db.realms_are_adjacent(
            int(target["realm_id"]), int(pact["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._engine._require_cross_valley_caught_up(
            int(target["realm_id"]), int(pact["realm_id"])
        )
        members = self._db.pact_members(pact["id"])
        if len(members) >= B.PACT_SIZE_MAX:
            raise ValueError("Пакт полон")
        with self._db.transaction():
            target = self._db.get_fief(target_fief_id)
            pact = self._db.get_pact(invite["pact_id"])
            if not target:
                raise ValueError("Усадьба не найдена")
            if not pact:
                raise ValueError("Пакт распущен")
            self._engine._require_cross_valley_caught_up(
                int(target["realm_id"]), int(pact["realm_id"])
            )
            claimed = self._db.claim_open_pact_invite(invite_id, "accepted")
            if not claimed:
                raise ValueError("Приглашение недоступно")
            members = self._db.pact_members(pact["id"])
            if len(members) >= B.PACT_SIZE_MAX:
                raise ValueError("Пакт полон")
            target = self._db.get_fief(target_fief_id)
            if target.get("pact_id"):
                raise ValueError("Вы уже в пакте")
            self._db.update_fief(target_fief_id, pact_id=pact["id"], cover_allies=False)
        return f"Вы в пакте \"{pact['name']}\"."

    def decline_pact_invite(self, actor_fief_id: int, invite_id: int) -> str:
        invite = self._db.get_pact_invite(invite_id)
        if not invite or invite["status"] != "open":
            raise ValueError("Приглашение недоступно")
        actor = self._db.get_fief(actor_fief_id)
        if not actor:
            raise ValueError("Усадьба не найдена")
        is_target = int(invite["target_fief_id"]) == int(actor_fief_id)
        is_inviter = int(invite["inviter_fief_id"]) == int(actor_fief_id)
        if not is_target and not is_inviter:
            raise ValueError("Нельзя отклонить чужое приглашение")
        status = "cancelled" if is_inviter and not is_target else "declined"
        claimed = self._db.claim_open_pact_invite(invite_id, status)
        if not claimed:
            raise ValueError("Приглашение недоступно")
        return "Приглашение отклонено." if status == "declined" else "Приглашение отменено."

    def leave_pact(self, fief_id: int) -> str:
        fief = self._db.get_fief(fief_id)
        if not fief.get("pact_id"):
            raise ValueError("Вы не в пакте")
        self._engine._require_action_window(int(fief["realm_id"]))
        with self._db.transaction():
            fief = self._db.get_fief(fief_id)
            if not fief.get("pact_id"):
                raise ValueError("Вы не в пакте")
            self._engine._require_action_window(int(fief["realm_id"]))
            pact_id = fief["pact_id"]
            pact = self._db.get_pact(pact_id)
            remaining = [
                m
                for m in self._db.pact_members(pact_id)
                if int(m["id"]) != int(fief_id)
            ]
            realm = self._db.get_realm(int(fief["realm_id"])) or {}
            tick_index = int(realm.get("tick_index") or 0)
            if len(remaining) < B.PACT_SIZE_MIN:
                # Роспуск: эскроу по payload.pact_id, в т.ч. у уже вышедших.
                wid = self._engine._world_id_for_realm(int(fief["realm_id"]))
                self._engine._cover_stances.refund_cover_stances_for_pact(
                    int(pact_id),
                    world_id=int(wid),
                    tick_index=int(tick_index),
                )
                for member in remaining:
                    self._db.update_fief(
                        int(member["id"]),
                        cover_allies=False,
                    )
                self._db.update_fief(
                    fief_id,
                    pact_id=None,
                    cover_allies=False,
                )
                self._db.dissolve_pact(pact_id)
                return "Вы вышли. Пакт распущен (меньше 2 участников)."
            # До lock - вернуть open; locked остаётся обязательством ночи.
            self._engine._cover_stances.refund_cover_stances_for_fief(
                int(fief_id), statuses=("open",)
            )
            self._db.update_fief(
                fief_id,
                pact_id=None,
                cover_allies=False,
            )
            if pact and pact["founder_fief_id"] == fief_id and remaining:
                self._db.update_pact(
                    pact_id, founder_fief_id=remaining[0]["id"]
                )
            return "Вы вышли из пакта."

    def set_cover(self, fief_id: int, enabled: bool) -> str:
        """Совместимость: вкл → ANY мин. бюджет; выкл → в стороне (через заставу)."""
        if not enabled:
            return self._engine._cover_stances.set_stand_down(fief_id)
        return self._engine._cover_stances.set_cover_stance(
            fief_id,
            mode=COVER_MODE_ANY,
            budget=int(B.COVER_BUDGET_MIN),
        )
