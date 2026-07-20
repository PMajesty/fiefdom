"""Ночной resolve набегов: дорога -> осада."""
from __future__ import annotations

from app.repos import NightRaidRepos

import random

from app import balance as B
from app.domain.raids import (
    RaidActionResult,
    RaidNightPartyNotice,
    ResolveNightReport,
    own_headcount_rumor,
    own_loss_rumor_band,
    resolve_raid,
    standing_raid_defense,
)
from app.domain.road_skirmish import (
    MemberRoadFate,
    RaidStack,
    RoadContestResult,
    _split_pool_proportional,
    build_coalitions,
    resolve_road_contest,
    split_loot_by_commit,
)
from app.domain.resource_bags import (
    capped_receive_amount,
    stash_amount,
    stash_from_row,
)
from app.domain.resource_format import format_victim_loot_sentence

from app.domain.ticks import tick_active
from app.resource_schema import raid_stolen_fields


class NightRaidResolver:
    def __init__(self, engine, db: NightRaidRepos) -> None:
        self._engine = engine
        self._db = db

    def _pick_raid_interceptor(
        self, vic: dict, *, incomplete_world: bool
    ) -> dict | None:
        if not vic.get("pact_id"):
            return None
        for m in self._db.pact_members(vic["pact_id"]):
            if m["id"] == vic["id"]:
                continue
            if not m.get("cover_allies"):
                continue
            if incomplete_world and int(m["realm_id"]) != int(vic["realm_id"]):
                continue
            if int(m.get("might") or 0) >= B.INTERCEPT_MIGHT:
                return m
        return None

    def _siege_probe_would_succeed(
        self,
        *,
        attack_might: int,
        watch_def: float,
        patrol: bool,
        fog: bool,
        victim_might: int,
        intercept: bool,
    ) -> bool:
        defense = standing_raid_defense(
            watch_defense=watch_def,
            victim_might=victim_might,
            patrol_active=patrol,
            fog_ignores_patrol=fog,
            intercept=intercept,
        )
        from app.domain.raids import raid_ratio

        return raid_ratio(attack_might, defense) >= B.RAID_SUCCESS_R

    def resolve_pending_raids(
        self, world_id: int, tick_index: int
    ) -> ResolveNightReport:
        """Ночной батч: дорога → осада. Только из run_world_tick / resume."""
        report = ResolveNightReport()
        if self._engine.world_tick_incomplete(int(world_id)):
            return report
        intents = self._db.list_raid_intents(
            int(world_id), int(tick_index), statuses=("open", "locked")
        )
        if not intents:
            return report

        by_victim: dict[int, list[dict]] = {}
        for intent in intents:
            payload = intent.get("payload") or {}
            vid = int(payload.get("victim_id") or 0)
            if vid <= 0:
                claimed = self._db.claim_resolve_action_intent(int(intent["id"]))
                if claimed:
                    report.resolved_count += 1
                continue
            by_victim.setdefault(vid, []).append(intent)

        for victim_id in sorted(by_victim.keys()):
            group = by_victim[victim_id]
            self._resolve_victim_night(
                world_id=int(world_id),
                tick_index=int(tick_index),
                victim_id=victim_id,
                intents=group,
                report=report,
            )
        return report

    def _resolve_victim_night(
        self,
        *,
        world_id: int,
        tick_index: int,
        victim_id: int,
        intents: list[dict],
        report: ResolveNightReport,
    ) -> None:
        vic = self._db.get_fief(victim_id)
        if not vic:
            for intent in intents:
                payload = intent.get("payload") or {}
                might = int(payload.get("might") or 0)
                with self._db.transaction():
                    if not self._db.claim_resolve_action_intent(int(intent["id"])):
                        continue
                    if might > 0:
                        self._db.credit_fief_resources(
                            int(intent["fief_id"]), might=might
                        )
                report.resolved_count += 1
            return

        stacks: list[RaidStack] = []
        intent_by_id = {int(i["id"]): i for i in intents}
        for intent in intents:
            payload = intent.get("payload") or {}
            stacks.append(
                RaidStack(
                    intent_id=int(intent["id"]),
                    fief_id=int(intent["fief_id"]),
                    might=int(payload.get("might") or 0),
                    pact_id=(
                        int(payload["attacker_pact_id"])
                        if payload.get("attacker_pact_id") is not None
                        else None
                    ),
                    open_truce=bool(payload.get("open_truce")),
                )
            )

        coalitions = build_coalitions(stacks)
        coal_by_key = {c.key: c for c in coalitions}
        vic_label = self._engine.fief_label(vic)

        # Если дорога уже записана в payload (crash resume) - не пересчитываем.
        planned = all(
            bool((intent_by_id[s.intent_id].get("payload") or {}).get("road_planned"))
            for s in stacks
        )
        if planned:
            fates = []
            for s in stacks:
                p = intent_by_id[s.intent_id].get("payload") or {}
                fates.append(
                    MemberRoadFate(
                        intent_id=int(s.intent_id),
                        fief_id=int(s.fief_id),
                        commit=int(p.get("might") or s.might),
                        road_deaths=int(p.get("road_deaths") or 0),
                        fled=bool(p.get("fled")),
                        siege_eligible=bool(p.get("siege_eligible")),
                    )
                )
            siege_key = None
            siege_pool = 0
            for f in fates:
                if f.siege_eligible:
                    siege_pool += max(0, f.commit - f.road_deaths)
                    # ключ коалиции лидера - из первого siege-eligible
                    if siege_key is None:
                        for c in coalitions:
                            if any(m.intent_id == f.intent_id for m in c.members):
                                siege_key = c.key
                                break
            road = RoadContestResult(
                member_fates=fates,
                siege_coalition_key=siege_key if siege_pool > 0 else None,
                siege_pool=siege_pool,
                public_road_line=str(
                    (intent_by_id[stacks[0].intent_id].get("payload") or {}).get(
                        "road_public_line"
                    )
                    or ""
                ),
            )
        else:
            road = resolve_road_contest(coalitions, victim_label=vic_label)
            with self._db.transaction():
                for fate in road.member_fates:
                    intent = intent_by_id.get(fate.intent_id)
                    if not intent:
                        continue
                    payload = dict(intent.get("payload") or {})
                    payload.update(
                        {
                            "road_planned": True,
                            "road_deaths": fate.road_deaths,
                            "fled": fate.fled,
                            "siege_eligible": fate.siege_eligible,
                            "road_public_line": road.public_road_line,
                        }
                    )
                    self._db.update_action_intent_payload(fate.intent_id, payload)
                    intent["payload"] = payload

        fate_by_intent = {f.intent_id: f for f in road.member_fates}

        if road.public_road_line and not planned:
            realm_ids = {
                int((intent_by_id[f.intent_id].get("payload") or {}).get(
                    "attacker_realm_id"
                ) or 0)
                for f in road.member_fates
            }
            realm_ids.add(int(vic["realm_id"]))
            for rid in sorted(r for r in realm_ids if r):
                self._engine._append_pending_raid_line(rid, road.public_road_line)
                report.notices.append(
                    RaidNightPartyNotice(
                        user_id=None,
                        realm_id=rid,
                        text=f"⚔️ {road.public_road_line}",
                        kind="public",
                    )
                )

        # Беглецы и проигравшие дороги: вернуть остаток, без осады.
        for fate in road.member_fates:
            if fate.siege_eligible:
                continue
            intent = intent_by_id.get(fate.intent_id)
            if not intent:
                continue
            returned = max(0, fate.commit - fate.road_deaths)
            payload = dict(intent.get("payload") or {})
            payload.update(
                {
                    "outcome": "flee" if fate.fled else "road_loss",
                    "road_deaths": fate.road_deaths,
                    "returned_might": returned,
                }
            )
            with self._db.transaction():
                claimed = self._db.claim_resolve_action_intent(fate.intent_id)
                if not claimed:
                    continue
                if returned > 0:
                    self._db.credit_fief_resources(fate.fief_id, might=returned)
                self._db.update_action_intent_payload(fate.intent_id, payload)
                atk = self._db.get_fief(fate.fief_id)
                if atk:
                    if fate.fled:
                        public = (
                            f"Отряд {self._engine.fief_label(atk)} развернулся "
                            f"на дороге к хутору {vic_label}"
                        )
                    else:
                        public = (
                            f"Отряд {self._engine.fief_label(atk)} схватился на дороге "
                            f"к хутору {vic_label}"
                        )
                    self._db.log_raid(
                        realm_id=int(atk["realm_id"]),
                        victim_realm_id=int(vic["realm_id"]),
                        attacker_fief_id=fate.fief_id,
                        victim_fief_id=victim_id,
                        success=False,
                        might_spent=fate.commit,
                        public_line=public,
                        tick_index=tick_index,
                    )
                    atk_line = public
                    via = bool(payload.get("via_portal"))
                    if via:
                        atk_realm = self._db.get_realm(atk["realm_id"]) or {}
                        vic_realm = self._db.get_realm(vic["realm_id"]) or {}
                        atk_line = (
                            f"В \"{vic_realm.get('title') or 'Долина'}\": {public}"
                        )
                        vic_line = (
                            f"Из \"{atk_realm.get('title') or 'Долина'}\": {public}"
                        )
                        self._engine._append_pending_raid_line(
                            int(atk["realm_id"]), atk_line
                        )
                        self._engine._append_pending_raid_line(
                            int(vic["realm_id"]), vic_line
                        )
                    else:
                        self._engine._append_pending_raid_line(
                            int(atk["realm_id"]), atk_line
                        )
            report.resolved_count += 1
            atk = self._db.get_fief(fate.fief_id)
            if atk:
                rumor = own_loss_rumor_band(fate.road_deaths, fate.commit)
                head = own_headcount_rumor(returned, fate.commit)
                if fate.fled:
                    text = (
                        f"Ваш отряд развернулся на дороге к хутору {vic_label}. "
                        f"{rumor} {head}"
                    ).strip()
                else:
                    text = (
                        f"На дороге к хутору {vic_label} вас оттеснили. "
                        f"{rumor} {head}"
                    ).strip()
                report.notices.append(
                    RaidNightPartyNotice(
                        user_id=int(atk["user_id"]),
                        realm_id=None,
                        text=text,
                        kind="dm",
                    )
                )

        if road.siege_coalition_key is None or road.siege_pool <= 0:
            return

        leader = coal_by_key[road.siege_coalition_key]
        siege_members = [
            fate_by_intent[m.intent_id]
            for m in leader.members
            if fate_by_intent.get(m.intent_id)
            and fate_by_intent[m.intent_id].siege_eligible
        ]
        if not siege_members:
            return

        # Щит на момент осады (до сдвига часов).
        vic = self._db.get_fief(victim_id) or vic
        if tick_active(vic.get("shield_until_tick"), tick_index):
            for fate in siege_members:
                returned = max(0, fate.commit - fate.road_deaths)
                with self._db.transaction():
                    claimed = self._db.claim_resolve_action_intent(fate.intent_id)
                    if not claimed:
                        continue
                    if returned > 0:
                        self._db.credit_fief_resources(fate.fief_id, might=returned)
                report.resolved_count += 1
                atk = self._db.get_fief(fate.fief_id)
                if atk:
                    report.notices.append(
                        RaidNightPartyNotice(
                            user_id=int(atk["user_id"]),
                            realm_id=None,
                            text=(
                                f"У хутора {vic_label} стоит щит - "
                                "ваш отряд вернулся без боя."
                            ),
                            kind="dm",
                        )
                    )
            return

        post_road_by_fief = {
            f.fief_id: max(0, f.commit - f.road_deaths) for f in siege_members
        }
        attack_pool = sum(post_road_by_fief.values())
        if attack_pool <= 0:
            for fate in siege_members:
                with self._db.transaction():
                    if self._db.claim_resolve_action_intent(fate.intent_id):
                        report.resolved_count += 1
            return

        # Кулдаун с прошлых ночей; логи этой же ночи не блокируют взаимные удары.
        for fate in siege_members:
            last_pair = self._db.last_raid_attacker_victim(fate.fief_id, victim_id)
            last_reverse = self._db.last_raid_attacker_victim(victim_id, fate.fief_id)
            blocked = False
            for raid_tick in (last_pair, last_reverse):
                if raid_tick is None:
                    continue
                if int(raid_tick) >= int(tick_index):
                    continue
                if int(raid_tick) + B.RAID_SAME_VICTIM_TICKS >= tick_index:
                    blocked = True
                    break
            if blocked:
                for f2 in siege_members:
                    returned = max(0, f2.commit - f2.road_deaths)
                    with self._db.transaction():
                        claimed = self._db.claim_resolve_action_intent(f2.intent_id)
                        if not claimed:
                            continue
                        if returned > 0:
                            self._db.credit_fief_resources(f2.fief_id, might=returned)
                    report.resolved_count += 1
                    atk = self._db.get_fief(f2.fief_id)
                    if atk:
                        report.notices.append(
                            RaidNightPartyNotice(
                                user_id=int(atk["user_id"]),
                                realm_id=None,
                                text=(
                                    f"Кулдаун на пару с хутором {vic_label} - "
                                    "ваш отряд вернулся без осады."
                                ),
                                kind="dm",
                            )
                        )
                return

        self._engine.collect_for_fief(victim_id, include_might=False)
        vic = self._db.get_fief(victim_id) or vic
        atk_realm_id = int(
            (intent_by_id[siege_members[0].intent_id].get("payload") or {}).get(
                "attacker_realm_id"
            )
            or (self._db.get_fief(siege_members[0].fief_id) or {}).get("realm_id")
            or 0
        )
        realm = self._db.get_realm(atk_realm_id) or {}
        vic_realm = self._db.get_realm(vic["realm_id"]) or realm
        fog = self._engine.realm_modifiers(realm).fog_ignores_patrol() or (
            self._engine.realm_modifiers(vic_realm).fog_ignores_patrol()
        )
        watch_def = self._engine.fief_prod(vic).defense
        patrol = tick_active(vic.get("patrol_until_tick"), tick_index)
        incomplete_world = self._engine.world_tick_incomplete(world_id)
        interceptor = self._engine._pick_raid_interceptor(
            vic, incomplete_world=incomplete_world
        )

        lead_fief = leader.lead_fief_id
        rng = random.Random(
            f"{world_id}:{tick_index}:{victim_id}:{lead_fief}"
        )
        atk_names = []
        for fate in siege_members:
            af = self._db.get_fief(fate.fief_id)
            if af:
                atk_names.append(self._engine.fief_label(af))
        atk_label = ", ".join(atk_names) if atk_names else "Отряд"
        vic_label = self._engine.fief_label(vic)
        vic_prod = self._engine.fief_prod(vic)

        # Сначала проба без перехвата: chip-fail не тратит INTERCEPT_MIGHT.
        use_intercept = False
        if interceptor is not None:
            if self._engine._siege_probe_would_succeed(
                attack_might=attack_pool,
                watch_def=watch_def,
                patrol=patrol,
                fog=fog,
                victim_might=int(vic.get("might") or 0),
                intercept=False,
            ):
                use_intercept = True
            else:
                interceptor = None

        result = resolve_raid(
            attacker_name=atk_label,
            victim_name=vic_label,
            attack_might=attack_pool,
            watch_defense=watch_def,
            patrol_active=patrol,
            intercept=use_intercept,
            victim_stash=stash_from_row(vic),
            barn_level=self._engine.barn_level(victim_id),
            victim_daily=vic_prod.resources(),
            fog_ignores_patrol=fog,
            victim_might=int(vic.get("might") or 0),
            rng=rng,
        )

        # Осада одной жертвы: перехват + все claim/credit/лут/щит в одной tx
        # (crash mid-victim не оставляет половину intents resolved).
        commits = {
            f.fief_id: post_road_by_fief[f.fief_id] for f in siege_members
        }
        member_settle: list[dict] = []
        any_success = False
        applied_total: dict[str, int] = {}

        with self._db.transaction():
            if use_intercept and interceptor is not None:
                if not self._db.debit_fief_resources(
                    int(interceptor["id"]), might=int(B.INTERCEPT_MIGHT)
                ):
                    interceptor = None
                    result = resolve_raid(
                        attacker_name=atk_label,
                        victim_name=vic_label,
                        attack_might=attack_pool,
                        watch_defense=watch_def,
                        patrol_active=patrol,
                        intercept=False,
                        victim_stash=stash_from_row(vic),
                        barn_level=self._engine.barn_level(victim_id),
                        victim_daily=self._engine.fief_prod(vic).resources(),
                        fog_ignores_patrol=fog,
                        victim_might=int(vic.get("might") or 0),
                        rng=rng,
                    )

            ordered_ids = sorted(commits.keys(), key=lambda i: (-commits[i], i))
            loss_parts = _split_pool_proportional(
                [commits[i] for i in ordered_ids], int(result.might_lost)
            )
            siege_loss_shares = {
                fid: part for fid, part in zip(ordered_ids, loss_parts)
            }
            loot_shares = (
                split_loot_by_commit(commits, dict(result.stolen))
                if result.success
                else {fid: {k: 0 for k in result.stolen} for fid in commits}
            )

            for fate in siege_members:
                post_road = post_road_by_fief[fate.fief_id]
                siege_loss = int(siege_loss_shares.get(fate.fief_id, 0))
                returned = max(0, post_road - siege_loss)
                stolen_bag = dict(loot_shares.get(fate.fief_id) or {})
                applied: dict[str, int] = {k: 0 for k in stolen_bag}
                payload = dict(
                    (intent_by_id[fate.intent_id].get("payload") or {})
                )
                payload.update(
                    {
                        "outcome": "success" if result.success else "fail",
                        "road_deaths": fate.road_deaths,
                        "returned_might": returned,
                    }
                )

                claimed = self._db.claim_resolve_action_intent(fate.intent_id)
                if not claimed:
                    continue
                if returned > 0:
                    self._db.credit_fief_resources(fate.fief_id, might=returned)

                if result.success and sum(stolen_bag.values()) > 0:
                    vic_live = self._db.get_fief(victim_id) or vic
                    atk_live = self._db.get_fief(fate.fief_id)
                    if atk_live:
                        barn = self._engine.barn_level(fate.fief_id)
                        cap = B.stash_cap(barn)
                        take_bag: dict[str, int] = {}
                        for key, amt in stolen_bag.items():
                            take = capped_receive_amount(
                                stash_amount(atk_live, key), int(amt), cap
                            )
                            take = min(
                                take, stash_amount(vic_live, key), int(amt)
                            )
                            take_bag[key] = max(0, take)
                        debit = {
                            k: v for k, v in take_bag.items() if v > 0
                        }
                        if debit and self._db.debit_fief_resources(
                            victim_id, debit
                        ):
                            self._db.credit_fief_resources(fate.fief_id, debit)
                            applied = debit
                            any_success = True
                            for key, amt in debit.items():
                                applied_total[key] = (
                                    int(applied_total.get(key, 0)) + int(amt)
                                )
                            vic = self._db.get_fief(victim_id) or vic

                self._db.update_action_intent_payload(fate.intent_id, payload)
                report.resolved_count += 1

                atk_live = self._db.get_fief(fate.fief_id)
                if atk_live:
                    total_lost = fate.road_deaths + siege_loss
                    rumor = own_loss_rumor_band(total_lost, fate.commit)
                    head = own_headcount_rumor(returned, fate.commit)
                    loss_rumor = f"{rumor} {head}".strip()
                    via = bool(payload.get("via_portal"))
                    public = result.public_line
                    atk_line = public
                    vic_line = public
                    if via:
                        atk_valley = (
                            self._db.get_realm(atk_live["realm_id"]) or {}
                        ).get("title") or "Долина"
                        vic_valley = (vic_realm.get("title") or "Долина")
                        atk_line = f"В \"{vic_valley}\": {public}"
                        vic_line = f"Из \"{atk_valley}\": {public}"
                    self._db.log_raid(
                        realm_id=int(atk_live["realm_id"]),
                        victim_realm_id=int(vic["realm_id"]),
                        attacker_fief_id=fate.fief_id,
                        victim_fief_id=victim_id,
                        success=bool(result.success),
                        might_spent=fate.commit,
                        public_line=atk_line,
                        tick_index=tick_index,
                        **raid_stolen_fields(applied),
                    )
                    self._engine._append_pending_raid_line(
                        int(atk_live["realm_id"]), atk_line
                    )
                    if via:
                        self._engine._append_pending_raid_line(
                            int(vic["realm_id"]), vic_line
                        )
                    elif int(atk_live["realm_id"]) != int(vic["realm_id"]):
                        self._engine._append_pending_raid_line(
                            int(vic["realm_id"]), vic_line
                        )
                    member_settle.append(
                        {
                            "fate": fate,
                            "returned": returned,
                            "siege_loss": siege_loss,
                            "applied": applied,
                            "payload": payload,
                            "atk_line": atk_line,
                            "vic_line": vic_line,
                            "via": via,
                            "loss_rumor": loss_rumor,
                            "atk_live": dict(atk_live),
                        }
                    )

            if result.success and any_success:
                # +1: часы сдвинутся на T+1 сразу после ночного resolve.
                self._db.update_fief(
                    victim_id,
                    shield_until=None,
                    shield_until_tick=(
                        tick_index + 1 + B.RAID_VICTIM_SHIELD_TICKS
                    ),
                )

        for item in member_settle:
            fate = item["fate"]
            applied = item["applied"]
            atk_live = item["atk_live"]
            atk_line = item["atk_line"]
            vic_line = item["vic_line"]
            via = item["via"]
            action = RaidActionResult(
                public_line=atk_line,
                success=result.success and sum(applied.values()) > 0
                if result.success
                else result.success,
                victim_fief_id=victim_id,
                victim_user_id=int(vic["user_id"]),
                victim_name=vic_label,
                attacker_name=self._engine.fief_label(atk_live),
                stolen=applied,
                intercept_applied=result.intercept_applied,
                interceptor_fief_id=(
                    int(interceptor["id"]) if interceptor else None
                ),
                interceptor_user_id=(
                    int(interceptor["user_id"]) if interceptor else None
                ),
                attacker_realm_id=int(atk_live["realm_id"]),
                victim_realm_id=int(vic["realm_id"]),
                via_portal=via,
                attacker_public_line=atk_line,
                victim_public_line=vic_line,
                might_committed=fate.commit,
                might_lost=fate.road_deaths + item["siege_loss"],
                road_deaths=fate.road_deaths,
                loss_rumor=item["loss_rumor"],
            )
            report.notices.append(
                RaidNightPartyNotice(
                    user_id=int(atk_live["user_id"]),
                    realm_id=None,
                    text=action.attacker_dm_text(),
                    kind="dm",
                )
            )
            report.notices.append(
                RaidNightPartyNotice(
                    user_id=None,
                    realm_id=int(atk_live["realm_id"]),
                    text=f"⚔️ {atk_line}",
                    kind="public",
                )
            )
            if via and int(vic["realm_id"]) != int(atk_live["realm_id"]):
                report.notices.append(
                    RaidNightPartyNotice(
                        user_id=None,
                        realm_id=int(vic["realm_id"]),
                        text=f"⚔️ {vic_line}",
                        kind="public",
                    )
                )

        if result.success and any_success:
            report.notices.append(
                RaidNightPartyNotice(
                    user_id=int(vic["user_id"]),
                    realm_id=None,
                    text=(
                        f"Ночью на ваш хутор ходили! "
                        f"{format_victim_loot_sentence(applied_total)}"
                    ),
                    kind="dm",
                )
            )
        elif not result.success:
            report.notices.append(
                RaidNightPartyNotice(
                    user_id=int(vic["user_id"]),
                    realm_id=None,
                    text=(
                        f"Ночью набег на ваш хутор отбит у ворот"
                        + (
                            " (союзник перехватил)."
                            if result.intercept_applied
                            else "."
                        )
                    ),
                    kind="dm",
                )
            )
        if result.intercept_applied and interceptor is not None:
            report.notices.append(
                RaidNightPartyNotice(
                    user_id=int(interceptor["user_id"]),
                    realm_id=None,
                    text=(
                        f"Вы перехватили ночной набег на хутор {vic_label}."
                        if not result.success
                        else (
                            f"Перехват не спас хутор {vic_label}: "
                            "враг ушёл с добычей."
                        )
                    ),
                    kind="dm",
                )
            )
