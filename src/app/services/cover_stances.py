"""Застава: stance declare/edit/cancel, midday freeze, night settle."""
from __future__ import annotations

from app.repos import CoverStanceRepos

from app import balance as B
from app.domain.cover import (
    COVER_MODE_ANY,
    COVER_MODE_SPECIFIC,
    COVER_MODE_STAND_DOWN,
    COVER_MODE_LABELS,
    CoverHelperOffer,
    filter_offers_for_victim,
    format_cover_receipt_names,
    select_cover_deployment,
)
from app.domain.raids import RaidNightPartyNotice
from app.domain.travel_supply import (
    PAYLOAD_SUPPLY_GRAIN,
    format_travel_supply_charge_line,
    intent_supply_grain,
    travel_supply_net_delta,
)


class CoverStanceService:
    def __init__(self, engine, db: CoverStanceRepos) -> None:
        self._engine = engine
        self._db = db

    def list_cover_stance_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]:
        raw = self._db.list_cover_stance_intents(
            int(world_id), int(tick_index), statuses=statuses
        )
        if not isinstance(raw, (list, tuple)):
            return []
        return list(raw)

    def list_open_cover_stance_intents_for_fief(self, fief_id: int) -> list[dict]:
        raw = self._db.list_open_cover_stance_intents_for_fief(int(fief_id))
        if not isinstance(raw, (list, tuple)):
            return []
        return list(raw)

    def lock_open_cover_stance_intents(self, world_id: int) -> int:
        world = self._db.get_world(world_id) or {}
        tick_index = int(world.get("tick_index") or 0)
        return self._db.lock_action_intents(
            int(world_id), tick_index, kind="cover_stance"
        )

    def _current_open_stance(self, fief_id: int, tick_index: int) -> dict | None:
        for intent in self.list_open_cover_stance_intents_for_fief(fief_id):
            if int(intent.get("tick_index") or -1) != int(tick_index):
                continue
            if intent.get("status") == "open":
                return intent
        return None

    def open_stance_escrow_preview(self, fief_id: int) -> tuple[int, int]:
        """Бюджет и уже списанное снабжение текущей open-стойки тика; иначе (0, 0)."""
        fief = self._db.get_fief(int(fief_id))
        if not fief:
            return 0, 0
        realm = self._db.get_realm(int(fief["realm_id"])) or {}
        tick_index = int(realm.get("tick_index") or 0)
        existing = self._current_open_stance(int(fief_id), tick_index)
        if not existing:
            return 0, 0
        payload = existing.get("payload") or {}
        budget = (
            int(payload.get("budget") or 0) if payload.get("escrowed") else 0
        )
        return budget, intent_supply_grain(payload)

    def _credit_cover_escrow(
        self,
        *,
        fief_id: int,
        payload: dict,
        refund_supply: bool,
    ) -> int:
        """Вернуть силу; зерно снабжения - только если refund_supply и оно было списано."""
        budget = int(payload.get("budget") or 0)
        credit: dict[str, int] = {}
        if budget > 0 and payload.get("escrowed"):
            credit["might"] = budget
        if refund_supply:
            supply = intent_supply_grain(payload)
            if supply > 0:
                credit["grain"] = supply
        if credit:
            self._db.credit_fief_resources(int(fief_id), **credit)
        return budget if "might" in credit else 0

    def _cancel_open_stance_refund(
        self, intent: dict, *, refund_supply: bool = True
    ) -> None:
        payload = dict(intent.get("payload") or {})
        claimed = self._db.cancel_action_intent(int(intent["id"]))
        if not claimed:
            raise ValueError("После закрытия заявок стойку уже не сменить")
        self._credit_cover_escrow(
            fief_id=int(intent["fief_id"]),
            payload=payload,
            refund_supply=refund_supply,
        )

    def refund_cover_stances_for_fief(
        self,
        fief_id: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> int:
        """Снять заставу в заданных статусах и вернуть эскроу.

        Зерно снабжения возвращается только для open (до midday lock).
        """
        refunded = 0
        allowed = set(statuses)
        for intent in self.list_open_cover_stance_intents_for_fief(int(fief_id)):
            if intent.get("status") not in allowed:
                continue
            payload = dict(intent.get("payload") or {})
            was_open = intent.get("status") == "open"
            claimed = self._db.cancel_action_intent(
                int(intent["id"]), statuses=tuple(allowed)
            )
            if not claimed:
                continue
            refunded += self._credit_cover_escrow(
                fief_id=int(intent["fief_id"]),
                payload=payload,
                refund_supply=was_open,
            )
        self._sync_cover_allies_flag(int(fief_id))
        return refunded

    def refund_cover_stances_for_pact(
        self,
        pact_id: int,
        *,
        world_id: int,
        tick_index: int,
    ) -> int:
        """Вернуть эскроу пакта (в т.ч. locked у вышедших).

        Зерно снабжения - только у open (роспуск в первой половине окна).
        """
        refunded = 0
        touched: set[int] = set()
        intents = self.list_cover_stance_intents(
            int(world_id),
            int(tick_index),
            statuses=("open", "locked"),
        )
        for intent in intents:
            payload = dict(intent.get("payload") or {})
            if int(payload.get("pact_id") or 0) != int(pact_id):
                continue
            was_open = intent.get("status") == "open"
            claimed = self._db.cancel_action_intent(
                int(intent["id"]), statuses=("open", "locked")
            )
            if not claimed:
                continue
            helper_fid = int(intent["fief_id"])
            refunded += self._credit_cover_escrow(
                fief_id=helper_fid,
                payload=payload,
                refund_supply=was_open,
            )
            touched.add(helper_fid)
        for fid in touched:
            self._sync_cover_allies_flag(fid)
        return refunded

    def _sync_cover_allies_flag(self, fief_id: int) -> None:
        """cover_allies = живой ANY (fallback авто-перехвата); SPECIFIC не включает."""
        has_any = False
        for intent in self.list_open_cover_stance_intents_for_fief(int(fief_id)):
            payload = dict(intent.get("payload") or {})
            if str(payload.get("mode") or "") != COVER_MODE_ANY:
                continue
            if int(payload.get("budget") or 0) > 0:
                has_any = True
                break
        self._db.update_fief(int(fief_id), cover_allies=has_any)

    def set_stand_down(self, fief_id: int) -> str:
        fief = self._engine.require_active_fief(fief_id)
        if not fief.get("pact_id"):
            raise ValueError("Нужен пакт")
        realm = self._db.get_realm(int(fief["realm_id"])) or {}
        tick_index = int(realm.get("tick_index") or 0)
        wid = self._engine._world_id_for_realm(int(fief["realm_id"]))
        world = self._db.get_world(wid) or {}
        if not self._engine.raid_declare_is_open(world):
            raise ValueError(
                "Поздно менять заставу: до закрытия заявок осталось меньше половины окна"
            )
        with self._db.transaction():
            if not self._engine.raid_declare_is_open(self._db.get_world(wid) or world):
                raise ValueError(
                    "Поздно менять заставу: до закрытия заявок осталось меньше половины окна"
                )
            existing = self._current_open_stance(fief_id, tick_index)
            if existing:
                self._cancel_open_stance_refund(existing)
            self._db.update_fief(fief_id, cover_allies=False)
        return "Застава: стоите в стороне этой ночью."

    def set_cover_stance(
        self,
        fief_id: int,
        *,
        mode: str,
        budget: int,
        target_fief_id: int | None = None,
    ) -> str:
        if mode not in (COVER_MODE_ANY, COVER_MODE_SPECIFIC):
            raise ValueError("Неизвестная стойка")
        if int(budget) < int(B.COVER_BUDGET_MIN):
            raise ValueError(f"Минимум {B.COVER_BUDGET_MIN} силы")
        fief = self._engine.require_active_fief(fief_id)
        if not fief.get("pact_id"):
            raise ValueError("Нужен пакт")
        if fief.get("hungry"):
            raise ValueError("Голодные мужики не воюют")
        if mode == COVER_MODE_SPECIFIC:
            if target_fief_id is None:
                raise ValueError("Укажите союзника")
            target = self._db.get_fief(int(target_fief_id))
            if not target or not target.get("pact_id"):
                raise ValueError("Союзник не найден")
            if int(target["pact_id"]) != int(fief["pact_id"]):
                raise ValueError("Это не ваш союзник по пакту")
            if int(target["id"]) == int(fief_id):
                raise ValueError("Нельзя стоять заставой у себя")
        realm = self._db.get_realm(int(fief["realm_id"])) or {}
        tick_index = int(realm.get("tick_index") or 0)
        wid = self._engine._world_id_for_realm(int(fief["realm_id"]))
        world = self._db.get_world(wid) or {}
        if not self._engine.raid_declare_is_open(world):
            raise ValueError(
                "Поздно менять заставу: до закрытия заявок осталось меньше половины окна"
            )

        self._engine.collect_for_fief(fief_id)
        fief = self._db.get_fief(fief_id) or fief
        if fief.get("hungry"):
            raise ValueError("Голодные мужики не воюют")
        lock_text = self._engine._format_raid_deadline(world, midpoint=True)
        new_supply = B.travel_supply_grain(int(budget))
        prior_budget, prior_supply = self.open_stance_escrow_preview(fief_id)
        available_might = int(fief.get("might") or 0) + prior_budget
        if available_might < int(budget):
            raise ValueError("Недостаточно силы")
        grain_delta = travel_supply_net_delta(
            prior_fee=prior_supply, new_fee=new_supply
        )
        if grain_delta > 0 and int(fief.get("grain") or 0) < grain_delta:
            raise ValueError("Недостаточно зерна на снабжение похода")

        with self._db.transaction():
            if not self._engine.raid_declare_is_open(self._db.get_world(wid) or world):
                raise ValueError(
                    "Поздно менять заставу: до закрытия заявок осталось меньше половины окна"
                )
            existing = self._current_open_stance(fief_id, tick_index)
            prior_supply = 0
            if existing:
                prior_supply = intent_supply_grain(existing.get("payload") or {})
                # Силу вернём; зерно снабжения сведём нетто к новой ставке.
                self._cancel_open_stance_refund(existing, refund_supply=False)
            fief = self._db.get_fief(fief_id) or fief
            if int(fief.get("might") or 0) < int(budget):
                raise ValueError("Недостаточно силы")
            grain_delta = travel_supply_net_delta(
                prior_fee=prior_supply, new_fee=new_supply
            )
            if grain_delta > 0 and int(fief.get("grain") or 0) < grain_delta:
                raise ValueError("Недостаточно зерна на снабжение похода")
            debit: dict[str, int] = {"might": int(budget)}
            if grain_delta > 0:
                debit["grain"] = int(grain_delta)
            if not self._db.debit_fief_resources(fief_id, **debit):
                fief_now = self._db.get_fief(fief_id) or fief
                if int(fief_now.get("might") or 0) < int(budget):
                    raise ValueError("Недостаточно силы")
                raise ValueError("Недостаточно зерна на снабжение похода")
            if grain_delta < 0:
                self._db.credit_fief_resources(fief_id, grain=int(-grain_delta))
            self._db.create_action_intent(
                world_id=int(wid),
                tick_index=tick_index,
                fief_id=int(fief_id),
                kind="cover_stance",
                status="open",
                payload={
                    "mode": mode,
                    "budget": int(budget),
                    "target_fief_id": (
                        int(target_fief_id)
                        if mode == COVER_MODE_SPECIFIC and target_fief_id
                        else None
                    ),
                    "pact_id": int(fief["pact_id"]),
                    "escrowed": True,
                    PAYLOAD_SUPPLY_GRAIN: int(new_supply),
                },
            )
            self._db.update_fief(
                fief_id, cover_allies=(mode == COVER_MODE_ANY)
            )

        label = COVER_MODE_LABELS.get(mode, mode)
        supply_line = format_travel_supply_charge_line(
            new_fee=int(new_supply), prior_fee=int(prior_supply)
        )
        if mode == COVER_MODE_SPECIFIC and target_fief_id:
            tgt = self._db.get_fief(int(target_fief_id))
            who = self._engine.fief_label(tgt) if tgt else str(target_fief_id)
            return (
                f"Застава: {label} ({who}), {budget} силы в резерве. "
                f"{supply_line} Сменить можно до {lock_text}."
            )
        return (
            f"Застава: {label}, {budget} силы в резерве. "
            f"{supply_line} Сменить можно до {lock_text}."
        )

    def cancel_cover_stance_intent(self, fief_id: int, intent_id: int) -> str:
        self._engine.require_active_fief(fief_id)
        intent = self._db.get_action_intent(int(intent_id))
        if not intent or intent.get("kind") != "cover_stance":
            raise ValueError("Стойка не найдена")
        if int(intent["fief_id"]) != int(fief_id):
            raise ValueError("Это не ваша стойка")
        if intent.get("status") != "open":
            raise ValueError("После закрытия заявок стойку уже не снять")
        payload = dict(intent.get("payload") or {})
        budget = int(payload.get("budget") or 0)
        supply = intent_supply_grain(payload)
        with self._db.transaction():
            self._cancel_open_stance_refund(intent, refund_supply=True)
            self._db.update_fief(fief_id, cover_allies=False)
        if supply > 0:
            return (
                f"Застава снята: {budget} силы и {supply} зерна снабжения вернулись. "
                "Стоите в стороне."
            )
        return f"Застава снята: {budget} силы вернулись. Стоите в стороне."

    def offers_from_intents(
        self, intents: list[dict], *, exclude_fief_id: int | None = None
    ) -> list[CoverHelperOffer]:
        offers: list[CoverHelperOffer] = []
        for intent in intents:
            payload = dict(intent.get("payload") or {})
            mode = str(payload.get("mode") or "")
            if mode not in (COVER_MODE_ANY, COVER_MODE_SPECIFIC):
                continue
            fid = int(intent["fief_id"])
            if exclude_fief_id is not None and fid == int(exclude_fief_id):
                continue
            helper = self._db.get_fief(fid)
            target_raw = payload.get("target_fief_id")
            offers.append(
                CoverHelperOffer(
                    fief_id=fid,
                    intent_id=int(intent["id"]),
                    mode=mode,
                    budget=int(payload.get("budget") or 0),
                    user_id=int(helper["user_id"]) if helper else None,
                    label=self._engine.fief_label(helper) if helper else str(fid),
                    target_fief_id=(
                        int(target_raw) if target_raw is not None else None
                    ),
                )
            )
        return offers

    def deploy_for_victim(
        self,
        *,
        world_id: int,
        tick_index: int,
        victim: dict,
        incomplete_world: bool,
    ):
        """Собрать и обрезать бюджеты пакта жертвы. None если пакта нет."""
        if not victim.get("pact_id"):
            return None
        intents = self.list_cover_stance_intents(
            int(world_id), int(tick_index), statuses=("open", "locked")
        )
        pact_id = int(victim["pact_id"])
        pact_intents = []
        for intent in intents:
            payload = intent.get("payload") or {}
            if int(payload.get("pact_id") or 0) != pact_id:
                continue
            helper = self._db.get_fief(int(intent["fief_id"]))
            if not helper:
                continue
            helper_pact = int(helper.get("pact_id") or 0)
            if helper_pact != pact_id:
                # После midday lock выход не снимает locked-обязательство ночи.
                if intent.get("status") != "locked":
                    continue
            if incomplete_world and int(helper["realm_id"]) != int(victim["realm_id"]):
                continue
            pact_intents.append(intent)
        offers = self.offers_from_intents(
            pact_intents, exclude_fief_id=int(victim["id"])
        )
        matching = filter_offers_for_victim(offers, victim_id=int(victim["id"]))
        return select_cover_deployment(matching)

    def settle_deployment(
        self,
        *,
        deployment,
        raid_success: bool,
        pact_members: list[dict],
        victim_id: int,
        report_notices: list,
        battle_refund_by_intent: dict[int, int] | None = None,
    ) -> str:
        """Списать эскроу за эту осаду; остаток бюджета оставить на другие набеги ночи.

        battle_refund_by_intent - возврат по схватке у ворот; None = вернуть весь deploy.
        """
        # raid_success оставлен для вызовов; кровь заставы считает gate clash.
        _ = raid_success
        deployed_by_intent: dict[int, int] = {}
        trimmed_by_intent: dict[int, int] = {}
        covered_labels: list[str] = []
        for helper in deployment.helpers:
            iid = int(helper.intent_id)
            deployed_by_intent[iid] = deployed_by_intent.get(iid, 0) + int(
                helper.budget
            )
            if helper.label:
                covered_labels.append(helper.label)
        for offer, amt in deployment.trimmed:
            iid = int(offer.intent_id)
            trimmed_by_intent[iid] = trimmed_by_intent.get(iid, 0) + int(amt)

        for intent_id in set(deployed_by_intent) | set(trimmed_by_intent):
            deployed = int(deployed_by_intent.get(intent_id, 0))
            remaining = int(trimmed_by_intent.get(intent_id, 0))
            if deployed <= 0:
                battle_refund = 0
            elif battle_refund_by_intent is not None:
                battle_refund = max(
                    0, int(battle_refund_by_intent.get(int(intent_id), 0))
                )
            else:
                battle_refund = deployed
            intent = self._db.get_action_intent(int(intent_id))
            if not intent or intent.get("status") not in ("open", "locked"):
                continue
            helper_fid = int(intent["fief_id"])
            payload = dict(intent.get("payload") or {})
            if remaining > 0:
                payload["budget"] = remaining
                updated = self._db.update_open_action_intent_payload(
                    int(intent_id), payload
                )
                if not updated:
                    continue
            else:
                claimed = self._db.claim_resolve_action_intent(int(intent_id))
                if not claimed:
                    continue
                self._sync_cover_allies_flag(helper_fid)
            if battle_refund > 0:
                self._db.credit_fief_resources(helper_fid, might=int(battle_refund))
            if deployed <= 0:
                continue
            helper_row = self._db.get_fief(helper_fid)
            if not helper_row:
                continue
            lost = max(0, deployed - int(battle_refund))
            report_notices.append(
                RaidNightPartyNotice(
                    user_id=int(helper_row["user_id"]),
                    realm_id=None,
                    text=(
                        f"Застава у ворот: из {deployed} силы вернулось {battle_refund}"
                        + (f", потери {lost}." if lost else ".")
                    ),
                    kind="dm",
                )
            )

        covered_ids = {int(h.fief_id) for h in deployment.helpers}
        stood_down: list[str] = []
        for member in pact_members:
            mid = int(member["id"])
            if mid == int(victim_id) or mid in covered_ids:
                continue
            has_stance = False
            for intent in self.list_open_cover_stance_intents_for_fief(mid):
                payload = dict(intent.get("payload") or {})
                mode = str(payload.get("mode") or "")
                if mode in (COVER_MODE_ANY, COVER_MODE_SPECIFIC) and int(
                    payload.get("budget") or 0
                ) > 0:
                    has_stance = True
                    break
            if not has_stance:
                stood_down.append(self._engine.fief_label(member))
        return format_cover_receipt_names(
            covered_labels=covered_labels,
            stood_down_labels=stood_down,
        )

    def resolve_remaining_cover_stances(
        self, world_id: int, tick_index: int
    ) -> list[RaidNightPartyNotice]:
        """Полный возврат эскроу для cover_stance, не забранных осадой."""
        notices: list[RaidNightPartyNotice] = []
        intents = self.list_cover_stance_intents(
            int(world_id), int(tick_index), statuses=("open", "locked")
        )
        for intent in intents:
            claimed = self._db.claim_resolve_action_intent(int(intent["id"]))
            if not claimed:
                continue
            payload = dict(claimed.get("payload") or {})
            budget = int(payload.get("budget") or 0)
            helper_fid = int(claimed["fief_id"])
            if budget > 0 and payload.get("escrowed"):
                self._db.credit_fief_resources(helper_fid, might=budget)
            self._sync_cover_allies_flag(helper_fid)
            helper = self._db.get_fief(helper_fid)
            if helper and budget > 0:
                notices.append(
                    RaidNightPartyNotice(
                        user_id=int(helper["user_id"]),
                        realm_id=None,
                        text=(
                            f"Застава не понадобилась: {budget} силы вернулись домой."
                        ),
                        kind="dm",
                    )
                )
        return notices
