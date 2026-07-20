"""Characterization: ночной resolve с живым resolve_raid (до peel NightRaidResolver)."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app import balance as B
from app.domain.raids import resolve_raid
from app.domain.road_skirmish import resolve_road_contest
from app.engine import Engine


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _base_fief(fid: int, *, realm_id: int, user_id: int, name: str, **extra) -> dict:
    row = {
        "id": fid,
        "realm_id": realm_id,
        "user_id": user_id,
        "name": name,
        "grain": 10,
        "goods": 10,
        "might": 20,
        "hungry": False,
        "last_raid_at": None,
        "last_raid_tick": None,
        "actions": 0,
        "pending_grain": 0.0,
        "pending_goods": 0.0,
        "pending_might": 0.0,
        "pact_id": None,
        "cover_allies": False,
        "shield_until": None,
        "shield_until_tick": None,
        "patrol_until": None,
        "patrol_until_tick": None,
        "last_active_at": _utcnow(),
        "last_active_tick": 0,
        "onboard_step": 4,
        "frozen": False,
    }
    row.update(extra)
    return row


def _raid_night_engine(
    *,
    fiefs: dict[int, dict],
    realms: dict[int, dict] | None = None,
    pair_log: dict[tuple[int, int], int] | None = None,
    pact_members: list[dict] | None = None,
    world_id: int = 1,
    tick_index: int = 10,
    watch_defense: float = 1.0,
):
    """Stateful harness: multi-fief/realm, sticky pending_raid_lines, stable labels."""
    fiefs = {int(k): dict(v) for k, v in fiefs.items()}
    if realms is None:
        realm_ids = {int(f["realm_id"]) for f in fiefs.values()}
        realms = {
            rid: {
                "id": rid,
                "world_id": world_id,
                "title": "Долина" if rid == 1 else f"Долина {rid}",
                "pending_raid_lines": [],
                "active_minor_key": None,
                "active_minor_until": None,
                "tick_index": tick_index,
            }
            for rid in sorted(realm_ids)
        }
    else:
        realms = {int(k): dict(v) for k, v in realms.items()}
        for realm in realms.values():
            realm.setdefault("pending_raid_lines", [])
    pair_log = dict(pair_log or {})
    intents: list[dict] = []
    log_calls: list[dict] = []

    def get_fief(fid):
        row = fiefs.get(int(fid))
        return dict(row) if row else None

    def update_fief(fid, **fields):
        fiefs[int(fid)].update(fields)

    def debit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            if int(row.get(col) or 0) < int(amt):
                return None
            row[col] = int(row[col]) - int(amt)
        return dict(row)

    def credit_fief_resources(fid, amounts=None, **kwargs):
        row = fiefs[int(fid)]
        merged = dict(amounts or {})
        merged.update(kwargs)
        for col, amt in merged.items():
            row[col] = int(row.get(col) or 0) + int(amt)
        return dict(row)

    def get_realm(rid):
        row = realms.get(int(rid))
        return dict(row) if row else None

    def update_realm(rid, **fields):
        realms[int(rid)].update(fields)

    def create_action_intent(**fields):
        row = {
            "id": len(intents) + 1,
            "world_id": fields["world_id"],
            "tick_index": fields["tick_index"],
            "fief_id": fields["fief_id"],
            "kind": fields["kind"],
            "payload": dict(fields.get("payload") or {}),
            "status": fields.get("status", "open"),
        }
        intents.append(row)
        return dict(row)

    def list_raid_intents(wid, tick, statuses=("open", "locked")):
        return [
            dict(i)
            for i in intents
            if int(i["world_id"]) == int(wid)
            and int(i["tick_index"]) == int(tick)
            and i["status"] in statuses
        ]

    def claim_resolve_action_intent(iid):
        for i in intents:
            if int(i["id"]) == int(iid) and i["status"] in ("open", "locked"):
                i["status"] = "resolved"
                return dict(i)
        return None

    def update_action_intent_payload(iid, payload):
        for i in intents:
            if int(i["id"]) == int(iid):
                i["payload"] = dict(payload)

    def log_raid(**kwargs):
        log_calls.append(dict(kwargs))
        pair_log[(kwargs["attacker_fief_id"], kwargs["victim_fief_id"])] = int(
            kwargs.get("tick_index") or tick_index
        )

    db = MagicMock()
    db.transaction = lambda: nullcontext()
    db.get_fief.side_effect = get_fief
    db.update_fief.side_effect = update_fief
    db.debit_fief_resources.side_effect = debit_fief_resources
    db.credit_fief_resources.side_effect = credit_fief_resources
    db.get_realm.side_effect = get_realm
    db.update_realm.side_effect = update_realm
    db.last_raid_attacker_victim.side_effect = lambda a, v: pair_log.get((a, v))
    db.log_raid.side_effect = log_raid
    db.pact_members.return_value = list(pact_members or [])
    db.fief_tiles.return_value = []
    db.realms_are_adjacent.return_value = True
    db.create_action_intent.side_effect = create_action_intent
    db.list_raid_intents.side_effect = list_raid_intents
    db.claim_resolve_action_intent.side_effect = claim_resolve_action_intent
    db.update_action_intent_payload.side_effect = update_action_intent_payload
    db.get_user.return_value = None
    db.get_active_events.return_value = []
    db.list_active_tile_entities.return_value = []
    db.get_world.return_value = {
        "id": world_id,
        "tick_index": tick_index,
        "tick_phase": "play",
        "timezone": "UTC",
        "play_opened_at": _utcnow(),
    }

    engine = Engine(db)
    engine.barn_level = MagicMock(return_value=0)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    prod = MagicMock()
    prod.defense = float(watch_defense)
    prod.resources.return_value = {
        B.RES_GRAIN: 5.0,
        B.RES_GOODS: 2.0,
        B.RES_MIGHT: 0.0,
    }
    engine.fief_prod = MagicMock(return_value=prod)
    engine._intents = intents
    engine._fiefs = fiefs
    engine._realms = realms
    engine._log_calls = log_calls
    engine._pair_log = pair_log
    return engine


def _inject_raid_intent(
    engine,
    *,
    fief_id: int,
    victim_id: int,
    might: int,
    world_id: int = 1,
    tick_index: int = 10,
    via_portal: bool = False,
    open_truce: bool = False,
    attacker_pact_id: int | None = None,
    status: str = "locked",
    **payload_extra,
) -> dict:
    atk = engine.db.get_fief(fief_id)
    vic = engine.db.get_fief(victim_id)
    assert atk and vic
    return engine.db.create_action_intent(
        world_id=world_id,
        tick_index=tick_index,
        fief_id=fief_id,
        kind="raid",
        status=status,
        payload={
            "victim_id": int(victim_id),
            "might": int(might),
            "open_truce": bool(open_truce),
            "via_portal": bool(via_portal),
            "attacker_realm_id": int(atk["realm_id"]),
            "victim_realm_id": int(vic["realm_id"]),
            "escrowed": True,
            "attacker_pact_id": attacker_pact_id,
            **payload_extra,
        },
    )


def _notice_tuples(report) -> list[tuple]:
    return [
        (n.kind, n.user_id, n.realm_id, n.text) for n in report.notices
    ]


def test_multi_stack_road_to_siege_live_resolve_pins_notices_loot_shield():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        grain=40,
        goods=20,
        might=5,
        pending_grain=30.0,
        pending_goods=12.0,
        pending_might=8.0,
        actions=1,
    )
    atk2 = _base_fief(3, realm_id=1, user_id=303, name="Второй", might=10)
    engine = _raid_night_engine(fiefs={1: atk, 2: vic, 3: atk2})
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=40)
    _inject_raid_intent(engine, fief_id=3, victim_id=2, might=30)

    report = engine.resolve_pending_raids(1, 10)

    assert report.resolved_count == 2
    assert _notice_tuples(report) == [
        ("public", None, 1, "⚔️ На дороге к хутору Жертва отряды схватились"),
        (
            "dm",
            303,
            None,
            "На дороге к хутору Жертва вас оттеснили. "
            "Свои потери чувствительные. Около половины дружины вернулась.",
        ),
        (
            "dm",
            101,
            None,
            "Вы ограбили Жертва: +14 зерна, +6 товаров.. "
            "Свои потери чувствительные. Около половины дружины вернулась. "
            "На дороге тоже потрепало.",
        ),
        ("public", None, 1, "⚔️ Атакующий ограбил Жертва"),
        (
            "dm",
            202,
            None,
            "Ночью на ваш хутор ходили! Унесено 14 зерна и 6 товаров.",
        ),
    ]
    assert engine._realms[1]["pending_raid_lines"] == [
        "На дороге к хутору Жертва отряды схватились",
        "Отряд Второй схватился на дороге к хутору Жертва",
        "Атакующий ограбил Жертва",
    ]
    assert engine._fiefs[2]["grain"] == 56
    assert engine._fiefs[2]["goods"] == 26
    assert engine._fiefs[2]["shield_until_tick"] == 10 + 1 + B.RAID_VICTIM_SHIELD_TICKS
    assert engine._fiefs[1]["might"] == 35
    assert engine._fiefs[3]["might"] == 32
    assert engine._log_calls[0]["attacker_fief_id"] == 3
    assert engine._log_calls[0]["success"] is False
    assert engine._log_calls[0]["might_spent"] == 30
    assert engine._log_calls[0]["public_line"] == (
        "Отряд Второй схватился на дороге к хутору Жертва"
    )
    assert engine._log_calls[1]["attacker_fief_id"] == 1
    assert engine._log_calls[1]["success"] is True
    assert engine._log_calls[1]["might_spent"] == 40
    assert engine._log_calls[1]["public_line"] == "Атакующий ограбил Жертва"


def test_crash_resume_reuses_road_planned_fates_no_reroll():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        grain=40,
        goods=20,
        might=5,
        pending_grain=0.0,
        pending_goods=0.0,
        pending_might=0.0,
    )
    engine = _raid_night_engine(fiefs={1: atk, 2: vic})
    _inject_raid_intent(
        engine,
        fief_id=1,
        victim_id=2,
        might=40,
        road_planned=True,
        road_deaths=0,
        fled=False,
        siege_eligible=True,
        road_public_line="К хутору Жертва странная дорога из кэша",
    )

    with patch(
        "app.engine.resolve_road_contest", wraps=resolve_road_contest
    ) as road_spy:
        report = engine.resolve_pending_raids(1, 10)

    assert road_spy.call_count == 0
    # Resume: public road fan-out не повторяется.
    assert not any(
        n.kind == "public" and "странная дорога" in n.text for n in report.notices
    )
    assert "странная дорога" not in engine._realms[1]["pending_raid_lines"]
    assert report.resolved_count == 1
    assert any(n.kind == "dm" and n.user_id == 101 for n in report.notices)


def test_flee_and_road_loss_attacker_dm_and_public_digest():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    atk2 = _base_fief(3, realm_id=1, user_id=303, name="Второй", might=10)
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        grain=40,
        goods=20,
        might=5,
        pending_grain=0.0,
        pending_goods=0.0,
        pending_might=0.0,
    )
    engine = _raid_night_engine(fiefs={1: atk, 2: vic, 3: atk2})
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=40)
    _inject_raid_intent(engine, fief_id=3, victim_id=2, might=10)

    report = engine.resolve_pending_raids(1, 10)

    flee_dm = (
        "Ваш отряд развернулся на дороге к хутору Жертва. "
        "Свои почти все вернулись. Большая часть дружины вернулась."
    )
    assert ("dm", 303, None, flee_dm) in _notice_tuples(report)
    # Flee: digest public line есть, public notice для бегства - нет.
    assert "Отряд Второй развернулся на дороге к хутору Жертва" in engine._realms[1][
        "pending_raid_lines"
    ]
    assert not any(
        n.kind == "public" and "развернулся" in n.text for n in report.notices
    )
    assert engine._fiefs[3]["might"] == 20  # 10 home + 10 returned


def test_siege_shield_gate_refunds_and_dm():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=5,
        shield_until_tick=11,
    )
    engine = _raid_night_engine(fiefs={1: atk, 2: vic})
    _inject_raid_intent(
        engine,
        fief_id=1,
        victim_id=2,
        might=40,
        road_planned=True,
        road_deaths=0,
        fled=False,
        siege_eligible=True,
        road_public_line="",
    )

    report = engine.resolve_pending_raids(1, 10)

    assert report.resolved_count == 1
    assert _notice_tuples(report) == [
        (
            "dm",
            101,
            None,
            "У хутора Жертва стоит щит - ваш отряд вернулся без боя.",
        )
    ]
    assert engine._fiefs[1]["might"] == 50  # 10 + 40 refund
    assert engine._log_calls == []


def test_siege_pair_cooldown_refunds_coalition_and_dm():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    vic = _base_fief(2, realm_id=1, user_id=202, name="Жертва", might=5)
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic},
        pair_log={(1, 2): 9},
    )
    _inject_raid_intent(
        engine,
        fief_id=1,
        victim_id=2,
        might=40,
        road_planned=True,
        road_deaths=0,
        fled=False,
        siege_eligible=True,
        road_public_line="",
    )

    report = engine.resolve_pending_raids(1, 10)

    assert report.resolved_count == 1
    assert _notice_tuples(report) == [
        (
            "dm",
            101,
            None,
            "Кулдаун на пару с хутором Жертва - ваш отряд вернулся без осады.",
        )
    ]
    assert engine._fiefs[1]["might"] == 50


def test_interceptor_chip_fail_skips_might_spend():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    vic = _base_fief(
        2,
        realm_id=1,
        user_id=202,
        name="Жертва",
        might=10,
        pact_id=50,
        grain=40,
        goods=20,
        pending_grain=0.0,
        pending_goods=0.0,
        pending_might=0.0,
    )
    ally = _base_fief(
        3,
        realm_id=1,
        user_id=303,
        name="Союзник",
        might=B.INTERCEPT_MIGHT + 2,
        pact_id=50,
        cover_allies=True,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic, 3: ally},
        pact_members=[vic, ally],
        watch_defense=1.0,
    )
    # attack=3 vs defense≈11 → chip-fail без intercept; перехватчик не тратится
    _inject_raid_intent(
        engine,
        fief_id=1,
        victim_id=2,
        might=3,
        road_planned=True,
        road_deaths=0,
        fled=False,
        siege_eligible=True,
        road_public_line="",
    )
    ally_before = engine._fiefs[3]["might"]

    report = engine.resolve_pending_raids(1, 10)

    assert engine._fiefs[3]["might"] == ally_before
    assert not any(
        n.user_id == 303 for n in report.notices
    ), "chip-fail must not emit interceptor DM"
    assert report.resolved_count == 1


def test_via_portal_night_public_and_flee_valley_prefixes():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=10)
    atk2 = _base_fief(3, realm_id=1, user_id=303, name="Второй", might=10)
    vic = _base_fief(
        2,
        realm_id=2,
        user_id=202,
        name="Жертва",
        grain=40,
        goods=20,
        might=5,
        pending_grain=0.0,
        pending_goods=0.0,
        pending_might=0.0,
    )
    realms = {
        1: {
            "id": 1,
            "world_id": 1,
            "title": "Север",
            "pending_raid_lines": [],
            "active_minor_key": None,
            "active_minor_until": None,
            "tick_index": 10,
        },
        2: {
            "id": 2,
            "world_id": 1,
            "title": "Юг",
            "pending_raid_lines": [],
            "active_minor_key": None,
            "active_minor_until": None,
            "tick_index": 10,
        },
    }
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic, 3: atk2},
        realms=realms,
    )
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=40, via_portal=True
    )
    _inject_raid_intent(
        engine, fief_id=3, victim_id=2, might=10, via_portal=True
    )

    report = engine.resolve_pending_raids(1, 10)

    flee_digest_atk = 'В "Юг": Отряд Второй развернулся на дороге к хутору Жертва'
    flee_digest_vic = 'Из "Север": Отряд Второй развернулся на дороге к хутору Жертва'
    assert flee_digest_atk in engine._realms[1]["pending_raid_lines"]
    assert flee_digest_vic in engine._realms[2]["pending_raid_lines"]
    public_texts = [n.text for n in report.notices if n.kind == "public"]
    assert any(t.startswith('⚔️ В "Юг":') for t in public_texts)
    assert any(t.startswith('⚔️ Из "Север":') for t in public_texts)


def test_h5_seed_two_draw_oracle_matches_cas_miss_stream():
    """Тот же rng-поток: первый resolve_raid (intercept) съедает draws второго."""
    import random

    seed = "1:10:2:1"
    stash = {B.RES_GRAIN: 40, B.RES_GOODS: 20, B.RES_MIGHT: 5}
    daily = {B.RES_GRAIN: 5.0, B.RES_GOODS: 2.0, B.RES_MIGHT: 0.0}
    kwargs = dict(
        attacker_name="Атакующий",
        victim_name="Жертва",
        attack_might=40,
        watch_defense=1.0,
        patrol_active=False,
        victim_stash=stash,
        barn_level=0,
        victim_daily=daily,
        fog_ignores_patrol=False,
        victim_might=5,
    )
    rng_stream = random.Random(seed)
    resolve_raid(**kwargs, intercept=True, rng=rng_stream)
    second = resolve_raid(**kwargs, intercept=False, rng=rng_stream)
    fresh = resolve_raid(**kwargs, intercept=False, rng=random.Random(seed))
    # Второй вызов на продолженном потоке отличается от свежего seed (H5).
    assert (second.might_lost, dict(second.stolen)) != (
        fresh.might_lost,
        dict(fresh.stolen),
    )
