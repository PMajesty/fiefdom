"""Обозы: объявление, отмена, ночной resolve."""
from __future__ import annotations

from app.repos import CaravanRepos

from app import balance as B
from app.domain.caravans import (
    DeclareCaravanResult,
    ResolveCaravanReport,
    caravan_is_public,
    format_caravan_bounce_public,
    format_caravan_declare_public,
    format_caravan_land_public,
)
from app.domain.raids import RaidNightPartyNotice
from app.domain.resource_bags import capped_receive_amount, stash_amount
from app.domain.resource_format import resource_name_ru, send_forbidden_message
from app.domain.resource_registry import tradeable_keys


class CaravanService:
    def __init__(self, engine, db: CaravanRepos) -> None:
        self._engine = engine
        self._db = db

    def resolve_target_fief(self, realm_id: int, text: str) -> dict | None:
        """Ищет усадьбу на всём континенте (своя долина + остальные долины мира)."""
        text = text.strip()
        realm_ids = {int(realm_id)}
        for nb in self._db.list_adjacent_realms(int(realm_id)):
            realm_ids.add(int(nb["id"]))
        if text.isdigit():
            f = self._db.get_fief(int(text))
            if f and int(f["realm_id"]) in realm_ids:
                return f
            return None
        needle = text.lower()
        for rid in sorted(realm_ids):
            for f in self._db.list_fiefs(rid):
                label = self._engine.fief_label(f)
                if f["name"].lower() == needle or label.lower() == needle:
                    return f
                user = self._db.get_user(f["user_id"])
                uname = (user.get("username") or "").strip().lower() if user else ""
                if uname and needle in {uname, f"@{uname}", f"усадьба @{uname}"}:
                    return f
        return None

    def caravan_intent_target_label(self, intent: dict) -> str:
        payload = intent.get("payload") or {}
        rid = int(payload.get("receiver_id") or 0)
        recv = self._db.get_fief(rid) if rid else None
        return self._engine.fief_label(recv) if recv else "?"

    def declare_caravan(
        self,
        from_fief_id: int,
        to_fief_id: int,
        res: str,
        amt: int,
    ) -> DeclareCaravanResult:
        """Обоз: списать сейчас, доставить в ночном resolve тика."""
        if res not in tradeable_keys():
            raise ValueError(send_forbidden_message())
        if amt <= 0:
            raise ValueError("Количество должно быть > 0")
        if from_fief_id == to_fief_id:
            raise ValueError("Нельзя передать себе")

        sender = self._engine.require_active_fief(from_fief_id)
        receiver = self._db.get_fief(to_fief_id)
        if not sender or not receiver:
            raise ValueError("Усадьба не найдена")
        if not self._db.realms_are_adjacent(
            int(sender["realm_id"]), int(receiver["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._engine._require_cross_valley_caught_up(
            int(sender["realm_id"]), int(receiver["realm_id"])
        )
        if int(sender["user_id"]) == int(receiver["user_id"]):
            raise ValueError("Нельзя передать своей другой усадьбе")
        if sender.get("frozen") or receiver.get("frozen"):
            raise ValueError("Усадьба недоступна")

        self._engine.collect_for_fief(from_fief_id)
        sender = self._db.get_fief(from_fief_id) or sender
        realm = self._db.get_realm(int(sender["realm_id"])) or {}
        tick_index = int(realm.get("tick_index") or 0)
        wid = self._engine._world_id_for_realm(int(sender["realm_id"]))
        world = self._db.get_world(wid) or {}
        if not self._engine.raid_declare_is_open(world):
            raise ValueError(
                "Поздно объявлять обоз: до закрытия заявок осталось меньше половины окна"
            )
        res_name = resource_name_ru(res)
        receiver_name = self._engine.fief_label(receiver)
        sender_name = self._engine.fief_label(sender)
        is_public = caravan_is_public(amt)
        lock_text = self._engine._format_raid_deadline(world, midpoint=True)
        resolve_text = self._engine._format_raid_deadline(world, midpoint=False)

        with self._db.transaction():
            self._engine._require_cross_valley_caught_up(
                int(sender["realm_id"]), int(receiver["realm_id"])
            )
            if not self._engine.raid_declare_is_open(self._db.get_world(wid) or world):
                raise ValueError(
                    "Поздно объявлять обоз: до закрытия заявок осталось меньше половины окна"
                )
            debited = self._db.debit_fief_resources(
                from_fief_id, **{res: int(amt)}
            )
            if not debited:
                raise ValueError("Недостаточно ресурса")
            intent = self._db.create_action_intent(
                world_id=wid,
                tick_index=tick_index,
                fief_id=from_fief_id,
                kind="caravan",
                status="open",
                payload={
                    "receiver_id": int(to_fief_id),
                    "res": str(res),
                    "amt": int(amt),
                    "escrowed": True,
                    "sender_realm_id": int(sender["realm_id"]),
                    "receiver_realm_id": int(receiver["realm_id"]),
                    "is_public": is_public,
                },
            )

        dm = (
            f"Обоз ушёл к {receiver_name}: {amt} {res_name} в пути. "
            f"Вернуть можно до {lock_text}. "
            f"Доставка после колокола тика около {resolve_text}."
        )
        recv_dm = (
            f"К вам идёт обоз от {sender_name}: {amt} {res_name}. "
            f"Прибудет после колокола тика."
        )
        public_text = None
        if is_public:
            public_text = format_caravan_declare_public(
                sender_name, receiver_name, amt, res_name
            )
        return DeclareCaravanResult(
            intent_id=int(intent["id"]),
            receiver_fief_id=int(to_fief_id),
            receiver_name=receiver_name,
            res=str(res),
            amt=int(amt),
            is_public=is_public,
            dm_text=dm,
            receiver_dm_text=recv_dm,
            public_declare_text=public_text,
        )

    def cancel_caravan_intent(self, fief_id: int, intent_id: int) -> str:
        fief = self._engine.require_active_fief(fief_id)
        intent = self._db.get_action_intent(int(intent_id))

        if not intent or intent.get("kind") != "caravan":
            raise ValueError("Обоз не найден")
        if int(intent["fief_id"]) != int(fief_id):
            raise ValueError("Это не ваш обоз")
        if intent.get("status") != "open":
            raise ValueError("После закрытия заявок обоз уже не вернуть")
        payload = dict(intent.get("payload") or {})
        res = str(payload.get("res") or "")
        amt = int(payload.get("amt") or 0)
        if res not in tradeable_keys() or amt <= 0:
            raise ValueError("Обоз повреждён")
        with self._db.transaction():
            claimed = self._db.cancel_action_intent(int(intent_id))
            if not claimed:
                raise ValueError("После закрытия заявок обоз уже не вернуть")
            self._db.credit_fief_resources(fief_id, **{res: amt})
        return (
            f"Обоз возвращён: {amt} {resource_name_ru(res)} снова у "
            f"{self._engine.fief_label(fief)}."
        )

    def resolve_pending_caravans(
        self, world_id: int, tick_index: int
    ) -> ResolveCaravanReport:
        """Ночной батч обозов. После набегов; только из close-play / resume."""
        report = ResolveCaravanReport()
        if self._engine.world_tick_incomplete(int(world_id)):
            return report
        intents = self._db.list_caravan_intents(
            int(world_id), int(tick_index), statuses=("open", "locked")
        )
        for intent in intents:
            claimed = self._db.claim_resolve_action_intent(int(intent["id"]))
            if not claimed:
                continue
            report.resolved_count += 1
            payload = dict(claimed.get("payload") or {})
            sender_id = int(claimed["fief_id"])
            receiver_id = int(payload.get("receiver_id") or 0)
            res = str(payload.get("res") or "")
            amt = int(payload.get("amt") or 0)
            is_public = bool(payload.get("is_public")) or caravan_is_public(amt)
            if res not in tradeable_keys() or amt <= 0 or receiver_id <= 0:
                if res in tradeable_keys() and amt > 0:
                    self._db.credit_fief_resources(sender_id, **{res: amt})
                continue

            sender = self._db.get_fief(sender_id)
            receiver = self._db.get_fief(receiver_id)
            sender_name = self._engine.fief_label(sender) if sender else "Усадьба"
            receiver_name = self._engine.fief_label(receiver) if receiver else "Усадьба"
            res_name = resource_name_ru(res)
            sender_realm = int(
                payload.get("sender_realm_id")
                or (sender or {}).get("realm_id")
                or 0
            )
            receiver_realm = int(
                payload.get("receiver_realm_id")
                or (receiver or {}).get("realm_id")
                or 0
            )

            landed = False
            if (
                receiver
                and not receiver.get("frozen")
                and sender
                and not sender.get("frozen")
            ):
                cap = B.stash_cap(self._engine.barn_level(receiver_id))
                held = stash_amount(receiver, res)
                free = max(0, cap - held)
                if free >= amt:
                    # Груз обоза целиком; ярмарочный бонус - сверх, по месту на складе.
                    self._db.credit_fief_resources(receiver_id, **{res: amt})
                    mods = self._engine.realm_modifiers(
                        self._db.get_realm(receiver_realm) if receiver_realm else None
                    )
                    bonus_amt = int(amt * mods.trade_bonus_frac())
                    if bonus_amt > 0:
                        live = self._db.get_fief(receiver_id) or receiver
                        add_bonus = capped_receive_amount(
                            stash_amount(live, res), bonus_amt, cap
                        )
                        if add_bonus:
                            self._db.credit_fief_resources(
                                receiver_id, **{res: add_bonus}
                            )
                    wedding_gift = mods.trade_gift_grain()
                    if wedding_gift:
                        for fid in (sender_id, receiver_id):
                            self._db.credit_fief_resources(
                                fid, **{B.RES_GRAIN: int(wedding_gift)}
                            )
                    landed = True
                else:
                    self._db.credit_fief_resources(sender_id, **{res: amt})
            else:
                self._db.credit_fief_resources(sender_id, **{res: amt})

            if landed:
                if sender and sender.get("user_id"):
                    report.notices.append(
                        RaidNightPartyNotice(
                            user_id=int(sender["user_id"]),
                            realm_id=None,
                            text=(
                                f"Ваш обоз дошёл до {receiver_name}: "
                                f"{amt} {res_name}."
                            ),
                            kind="dm",
                        )
                    )
                if receiver and receiver.get("user_id"):
                    report.notices.append(
                        RaidNightPartyNotice(
                            user_id=int(receiver["user_id"]),
                            realm_id=None,
                            text=(
                                f"К вам прибыл обоз от {sender_name}: "
                                f"{amt} {res_name}."
                            ),
                            kind="dm",
                        )
                    )
                if is_public:
                    public = format_caravan_land_public(
                        sender_name, receiver_name, amt, res_name
                    )
                    for rid in {sender_realm, receiver_realm}:
                        if rid:
                            report.notices.append(
                                RaidNightPartyNotice(
                                    user_id=None,
                                    realm_id=int(rid),
                                    text=public,
                                    kind="public",
                                )
                            )
                            report.digest_lines.append((int(rid), public))
            else:
                if sender and sender.get("user_id"):
                    report.notices.append(
                        RaidNightPartyNotice(
                            user_id=int(sender["user_id"]),
                            realm_id=None,
                            text=(
                                f"Обоз к {receiver_name} вернулся: "
                                f"{amt} {res_name} снова у вас "
                                f"(нет места или двор недоступен)."
                            ),
                            kind="dm",
                        )
                    )
                if receiver and receiver.get("user_id"):
                    report.notices.append(
                        RaidNightPartyNotice(
                            user_id=int(receiver["user_id"]),
                            realm_id=None,
                            text=(
                                f"Обоз от {sender_name} не приняли "
                                f"({amt} {res_name}) - склада не хватило "
                                f"или двор недоступен."
                            ),
                            kind="dm",
                        )
                    )
                if is_public:
                    public = format_caravan_bounce_public(
                        sender_name, receiver_name, amt, res_name
                    )
                    for rid in {sender_realm, receiver_realm}:
                        if rid:
                            report.notices.append(
                                RaidNightPartyNotice(
                                    user_id=None,
                                    realm_id=int(rid),
                                    text=public,
                                    kind="public",
                                )
                            )
                            report.digest_lines.append((int(rid), public))
        return report
