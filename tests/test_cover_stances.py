"""Part C: standing Застава - escrow, freeze, deploy caps, intercept fallback."""
from __future__ import annotations

import os
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.cover import (
    COVER_MODE_ANY,
    COVER_MODE_SPECIFIC,
    CoverDeployment,
    CoverHelperOffer,
    filter_offers_for_victim,
    format_cover_receipt_names,
    select_cover_deployment,
)
from app.domain.gate_clash import resolve_gate_clash
from app.domain.guide import game_guide
from app.domain.raids import resolve_raid
from app.engine import Engine
from tests.test_raid_night_characterization import (
    _base_fief,
    _inject_raid_intent,
    _raid_night_engine,
)


def test_select_cover_priority_specific_then_budget_and_helper_cap():
    offers = [
        CoverHelperOffer(1, 10, COVER_MODE_ANY, 20, label="A"),
        CoverHelperOffer(2, 11, COVER_MODE_SPECIFIC, 10, label="B", target_fief_id=9),
        CoverHelperOffer(3, 12, COVER_MODE_ANY, 25, label="C"),
        CoverHelperOffer(4, 13, COVER_MODE_SPECIFIC, 15, label="D", target_fief_id=9),
    ]
    matching = filter_offers_for_victim(offers, victim_id=9)
    dep = select_cover_deployment(matching, max_helpers=3)
    assert [h.fief_id for h in dep.helpers] == [4, 2, 3]
    assert dep.total == 50  # 15+10+25; A trimmed as 4th helper
    assert sum(a for _, a in dep.trimmed) == 20  # A full 20
    # Optional total cap still supported for experiments / old tests.
    capped = select_cover_deployment(matching, max_helpers=3, max_total=40)
    assert capped.total == 40
    assert sum(a for _, a in capped.trimmed) == 30


def test_cover_battle_refund_follows_gate_clash_not_flat_pct():
    gate = resolve_gate_clash(
        attack_pool=40,
        defense=30,
        home_might=10,
        cover_by_intent={7: 20},
    )
    assert gate.applied
    assert gate.cover_deaths_by_intent[7] == gate.cover_deaths_total
    refund = gate.cover_refund(7, 20)
    assert refund == 20 - gate.cover_deaths_total
    assert refund != int(20 * 0.5)
    assert refund != 20 - int(20 * 0.35)


def _cover_engine():
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=40)
    vic = _base_fief(
        2, realm_id=1, user_id=202, name="Жертва", might=8, pact_id=7, grain=30
    )
    ally = _base_fief(
        3,
        realm_id=1,
        user_id=303,
        name="Союзник",
        might=40,
        pact_id=7,
        cover_allies=False,
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic, 3: ally},
        pact_members=[vic, ally],
        watch_defense=1.0,
    )
    for realm in engine._realms.values():
        realm["day_number"] = 5
    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine._format_raid_deadline = MagicMock(return_value="12:00")
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine.collect_for_fief = MagicMock()
    engine.db.list_fiefs.side_effect = lambda rid: [
        dict(f) for f in engine._fiefs.values() if int(f["realm_id"]) == int(rid)
    ]
    engine.db.list_adjacent_realms.return_value = []
    return engine


def test_cover_stance_escrow_has_no_per_helper_cap():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=35)
    assert engine._fiefs[3]["might"] == 5
    intents = [i for i in engine._intents if i["kind"] == "cover_stance"]
    assert intents[0]["payload"]["budget"] == 35


def test_cover_stance_escrows_might_and_freezes_after_lock():
    engine = _cover_engine()
    msg = engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    assert "Застава" in msg
    assert engine._fiefs[3]["might"] == 30
    assert engine._fiefs[3]["cover_allies"] is True
    intents = [i for i in engine._intents if i["kind"] == "cover_stance"]
    assert len(intents) == 1
    assert intents[0]["status"] == "open"
    assert intents[0]["payload"]["budget"] == 10

    engine.raid_declare_is_open = MagicMock(return_value=False)
    with pytest.raises(ValueError, match="Поздно менять заставу"):
        engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    assert engine._fiefs[3]["might"] == 30

    intents[0]["status"] = "locked"
    with pytest.raises(ValueError, match="закрытия заявок"):
        engine.cancel_cover_stance_intent(3, intents[0]["id"])
    assert engine._fiefs[3]["might"] == 30


def test_stand_down_refunds_open_stance():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=8)
    assert engine._fiefs[3]["might"] == 32
    msg = engine.set_cover_stand_down(3)
    assert "стороне" in msg
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["cover_allies"] is False
    assert all(i["status"] == "cancelled" for i in engine._intents)


def test_same_pact_raid_rejected_and_hidden_from_targets():
    engine = _cover_engine()
    engine._fiefs[1]["pact_id"] = 7
    engine.db.pact_members.return_value = [
        engine._fiefs[1],
        engine._fiefs[2],
        engine._fiefs[3],
    ]
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine._require_cross_valley_caught_up = MagicMock()
    engine.raid_declare_is_open = MagicMock(return_value=True)
    with pytest.raises(ValueError, match="союзника по пакту"):
        engine.declare_raid(1, 2, 5)
    targets = engine.list_raid_target_fiefs(1)
    assert all(int(t["id"]) != 2 for t in targets)
    assert all(int(t["id"]) != 3 for t in targets)


def test_declare_raid_has_no_victim_or_ally_cover_ping():
    engine = _cover_engine()
    engine._spend_action = MagicMock()
    engine._require_cross_valley_caught_up = MagicMock()
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine._fiefs[1]["onboard_step"] = 4
    engine._realms[1]["day_number"] = 5
    engine.db.get_realm.side_effect = lambda rid: engine._realms.get(int(rid))
    result = engine.declare_raid(1, 2, 5)
    assert result.dm_text
    assert "застав" not in result.dm_text.lower()
    assert "прикр" not in result.dm_text.lower()
    assert getattr(result, "victim_dm_text", None) in (None, "")


def test_night_deployed_cover_skips_auto_intercept():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=5, status="locked")
    engine._fiefs[3]["cover_allies"] = True
    ally_might_before = engine._fiefs[3]["might"]

    with patch(
        "app.services.night_raids.resolve_raid", wraps=resolve_raid
    ) as spy:
        report = engine.resolve_pending_raids(1, 10)

    assert report.resolved_count >= 1
    assert spy.call_args.kwargs.get("reinforce_might", 0) == 20
    assert spy.call_args.kwargs.get("intercept") is False
    # Авто-перехват не дебетил двор: только settle заставы (refund после боя).
    assert engine._fiefs[3]["might"] >= ally_might_before
    assert engine._fiefs[3]["might"] != ally_might_before - B.INTERCEPT_MIGHT


def test_night_gate_clash_burns_cover_and_home_via_settle():
    """Живой night path: застава и дом теряют по схватке, не 50%/35%."""
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    cover_intent = next(i for i in engine._intents if i["kind"] == "cover_stance")
    cover_intent["status"] = "locked"
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=5, status="locked")
    ally_after_escrow = int(engine._fiefs[3]["might"])
    vic_home_before = int(engine._fiefs[2]["might"])
    assert ally_after_escrow == 20
    assert vic_home_before == 8

    gate = resolve_gate_clash(
        attack_pool=5,
        defense=1 + vic_home_before + 20,
        home_might=vic_home_before,
        cover_by_intent={int(cover_intent["id"]): 20},
    )
    assert gate.applied
    expected_ally = ally_after_escrow + gate.cover_refund(int(cover_intent["id"]), 20)
    expected_vic = vic_home_before - gate.home_deaths

    report = engine.resolve_pending_raids(1, 10)

    assert report.resolved_count >= 1
    assert engine._fiefs[3]["might"] == expected_ally
    assert engine._fiefs[2]["might"] == expected_vic
    assert expected_ally != ally_after_escrow + int(20 * 0.5)
    assert expected_ally != ally_after_escrow + (20 - int(20 * 0.35))
    cover_dm = [
        n.text
        for n in report.notices
        if n.kind == "dm" and n.user_id == 303 and "Застава у ворот" in n.text
    ]
    assert cover_dm
    lost = gate.cover_deaths_by_intent[int(cover_intent["id"])]
    refund = gate.cover_refund(int(cover_intent["id"]), 20)
    assert f"из 20 силы вернулось {refund}" in cover_dm[0]
    if lost:
        assert f"потери {lost}" in cover_dm[0]


def test_settle_deployment_uses_battle_refund_map():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    intent = next(i for i in engine._intents if i["kind"] == "cover_stance")
    intent["status"] = "locked"
    deployment = CoverDeployment(
        helpers=[
            CoverHelperOffer(
                3, int(intent["id"]), COVER_MODE_ANY, 20, label="Союзник"
            )
        ],
        total=20,
        trimmed=[],
    )
    notices: list = []
    engine._cover_stances.settle_deployment(
        deployment=deployment,
        raid_success=True,
        pact_members=[engine._fiefs[2], engine._fiefs[3]],
        victim_id=2,
        report_notices=notices,
        battle_refund_by_intent={int(intent["id"]): 17},
    )
    # После эскроу 20 дома осталось 20; вернули 17 из боя → 37.
    assert engine._fiefs[3]["might"] == 37
    assert any("вернулось 17" in n.text and "потери 3" in n.text for n in notices)


def test_night_intercept_only_when_deployed_cover_zero():
    ally = _base_fief(
        3,
        realm_id=1,
        user_id=303,
        name="Союзник",
        might=B.INTERCEPT_MIGHT + 2,
        pact_id=7,
        cover_allies=True,
    )
    vic = _base_fief(
        2, realm_id=1, user_id=202, name="Жертва", might=8, pact_id=7, grain=30
    )
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=40)
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic, 3: ally},
        pact_members=[vic, ally],
        watch_defense=1.0,
    )
    # Живые ссылки в pact_members на копии harness: синхронизируем через side_effect
    engine.db.pact_members.side_effect = lambda pid: [
        engine._fiefs[2],
        engine._fiefs[3],
    ]
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=3, status="locked")
    engine._siege_probe_would_succeed = MagicMock(return_value=True)

    with patch(
        "app.services.night_raids.resolve_raid",
        return_value=MagicMock(
            success=False,
            ratio=0.5,
            might_lost=1,
            stolen={B.RES_GRAIN: 0, B.RES_GOODS: 0},
            intercept_applied=True,
            public_line="отбит",
        ),
    ) as spy:
        engine.resolve_pending_raids(1, 10)

    assert spy.call_args.kwargs["intercept"] is True
    assert spy.call_args.kwargs.get("reinforce_might", 0) == 0
    assert engine._fiefs[3]["might"] == 2


def test_lock_travel_locks_raid_caravan_and_cover_without_telegraph():
    db = MagicMock()
    engine = Engine(db)
    db.lock_action_intents.return_value = 1
    n = engine.lock_open_travel_intents(1)
    assert n == 3
    assert db.lock_action_intents.call_count == 3
    assert not hasattr(engine, "take_pending_lock_notices")


def test_pact_rejoin_cooldown_after_leave():
    from tests.test_pact_invites import _pact_engine

    engine, fiefs, _invites, pact = _pact_engine()
    fiefs[2]["pact_id"] = pact["id"]
    engine.db.get_world.return_value = {"id": 1, "tick_phase": "play"}
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine.leave_pact(2)
    assert fiefs[2].get("pact_left_tick") == 5
    with pytest.raises(ValueError, match="подождите"):
        engine.create_pact(2, "Новый")


def _leave_pact_cover_engine():
    engine = _cover_engine()
    engine.db.get_world.return_value = {
        "id": 1,
        "tick_index": 10,
        "tick_phase": "play",
    }
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine._require_action_window = MagicMock()
    engine.db.get_pact.return_value = {
        "id": 7,
        "founder_fief_id": 2,
        "name": "Пакт",
    }
    def dissolve_pact(pid):
        for row in engine._fiefs.values():
            if row.get("pact_id") == pid:
                row["pact_id"] = None

    engine.db.dissolve_pact.side_effect = dissolve_pact
    engine.db.update_pact = MagicMock()
    other = _base_fief(
        4, realm_id=1, user_id=404, name="Ещё", might=10, pact_id=7
    )
    engine._fiefs[4] = other
    engine.db.pact_members.side_effect = lambda pid: [
        f
        for f in (engine._fiefs[2], engine._fiefs[3], engine._fiefs[4])
        if f.get("pact_id") == 7
    ]
    return engine


def test_leave_pact_before_lock_refunds_open_cover():
    engine = _leave_pact_cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    assert engine._fiefs[3]["might"] == 28
    engine.leave_pact(3)
    assert engine._fiefs[3]["pact_id"] is None
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["cover_allies"] is False
    assert all(
        i["status"] == "cancelled"
        for i in engine._intents
        if i["kind"] == "cover_stance"
    )
    dep = engine._cover_stances.deploy_for_victim(
        world_id=1,
        tick_index=10,
        victim=engine._fiefs[2],
        incomplete_world=False,
    )
    assert dep is not None
    assert dep.total == 0


def test_leave_pact_after_lock_keeps_locked_cover_commitment():
    engine = _leave_pact_cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    engine.leave_pact(3)
    assert engine._fiefs[3]["pact_id"] is None
    assert engine._fiefs[3]["might"] == 28
    assert any(
        i["status"] == "locked"
        for i in engine._intents
        if i["kind"] == "cover_stance"
    )
    dep = engine._cover_stances.deploy_for_victim(
        world_id=1,
        tick_index=10,
        victim=engine._fiefs[2],
        incomplete_world=False,
    )
    assert dep is not None
    assert dep.total == 12


def test_dissolve_refunds_locked_cover_of_prior_leaver():
    """Роспуск возвращает locked-эскроу тому, кто вышел после midday."""
    engine = _leave_pact_cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    engine.leave_pact(3)
    assert engine._fiefs[3]["pact_id"] is None
    assert engine._fiefs[3]["might"] == 28
    # Остались двое (2 и 4); выход 4 распускает пакт.
    engine.db.pact_members.side_effect = lambda pid: [
        f for f in (engine._fiefs[2], engine._fiefs[4]) if f.get("pact_id") == 7
    ]
    msg = engine.leave_pact(4)
    assert "распущен" in msg.lower()
    assert engine._fiefs[3]["might"] == 40
    assert all(
        i["status"] == "cancelled"
        for i in engine._intents
        if i["kind"] == "cover_stance"
    )
    assert engine._fiefs[2].get("pact_left_tick") == 10
    assert engine._fiefs[2]["cover_allies"] is False
    with pytest.raises(ValueError, match="подождите"):
        engine.create_pact(2, "Снова")


def test_create_pact_founder_starts_stand_down():
    from tests.test_pact_invites import _pact_engine

    engine, fiefs, _invites, _pact = _pact_engine()
    fiefs[2]["pact_id"] = None
    fiefs[2]["cover_allies"] = True
    engine.db.get_world.return_value = {"id": 1, "tick_phase": "play"}
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine.world_tick_incomplete = MagicMock(return_value=False)
    engine._require_action_window = MagicMock()

    def create_pact(realm_id, name, founder_fief_id):
        row = {"id": 99, "realm_id": realm_id, "name": name, "founder_fief_id": founder_fief_id}
        fiefs[int(founder_fief_id)]["pact_id"] = 99
        fiefs[int(founder_fief_id)]["cover_allies"] = False
        return row

    engine.db.create_pact.side_effect = create_pact
    msg = engine.create_pact(2, "Новый")
    assert "создан" in msg.lower()
    assert fiefs[2]["cover_allies"] is False


def test_specific_cover_does_not_enable_auto_intercept_for_other_victim():
    engine = _cover_engine()
    other = _base_fief(
        4, realm_id=1, user_id=404, name="Другая", might=8, pact_id=7, grain=20
    )
    engine._fiefs[4] = other
    engine.db.pact_members.side_effect = lambda pid: [
        engine._fiefs[2],
        engine._fiefs[3],
        engine._fiefs[4],
    ]
    engine.set_cover_stance(
        3, mode=COVER_MODE_SPECIFIC, budget=10, target_fief_id=2
    )
    assert engine._fiefs[3]["cover_allies"] is False
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    # Жертва 4: deploy 0; SPECIFIC-помощник не должен уйти в авто-перехват.
    picked = engine._pick_raid_interceptor(engine._fiefs[4], incomplete_world=False)
    assert picked is None


def test_cover_receipt_labels_stand_down_not_other_stances():
    assert format_cover_receipt_names(
        covered_labels=["Союзник"],
        stood_down_labels=["Сторож"],
    ) == "У ворот стояли: Союзник. В стороне: Сторож."
    engine = _cover_engine()
    specific = _base_fief(
        4,
        realm_id=1,
        user_id=404,
        name="Спец",
        might=30,
        pact_id=7,
    )
    idle = _base_fief(
        5,
        realm_id=1,
        user_id=505,
        name="В стороне",
        might=10,
        pact_id=7,
    )
    engine._fiefs[4] = specific
    engine._fiefs[5] = idle
    engine.db.pact_members.side_effect = lambda pid: [
        engine._fiefs[2],
        engine._fiefs[3],
        engine._fiefs[4],
        engine._fiefs[5],
    ]
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    engine.set_cover_stance(
        4, mode=COVER_MODE_SPECIFIC, budget=8, target_fief_id=5
    )
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    deployment = CoverDeployment(
        helpers=[
            CoverHelperOffer(
                3, engine._intents[0]["id"], COVER_MODE_ANY, 10, label="Союзник"
            )
        ],
        total=10,
        trimmed=[],
    )
    notices: list = []
    receipt = engine._cover_stances.settle_deployment(
        deployment=deployment,
        raid_success=False,
        pact_members=[
            engine._fiefs[2],
            engine._fiefs[3],
            engine._fiefs[4],
            engine._fiefs[5],
        ],
        victim_id=2,
        report_notices=notices,
    )
    assert "У ворот стояли: Союзник." in receipt
    assert "В стороне: В стороне." in receipt
    assert "Спец" not in receipt


def test_cover_allies_cleared_when_night_stance_resolves():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    assert engine._fiefs[3]["cover_allies"] is True
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    notices = engine.resolve_remaining_cover_stances(1, 10)
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["cover_allies"] is False
    assert notices
    # Без новой стойки авто-перехват не выбирает союзника.
    picked = engine._pick_raid_interceptor(engine._fiefs[2], incomplete_world=False)
    assert picked is None


def test_any_cover_first_siege_consumes_full_budget():
    """Без потолка суммы ANY уходит целиком в первую осаду ночи."""
    atk = _base_fief(1, realm_id=1, user_id=101, name="Атакующий", might=40)
    vic_a = _base_fief(
        2, realm_id=1, user_id=202, name="ЖертваА", might=8, pact_id=7, grain=30
    )
    vic_b = _base_fief(
        4, realm_id=1, user_id=404, name="ЖертваБ", might=8, pact_id=7, grain=30
    )
    ally = _base_fief(
        3, realm_id=1, user_id=303, name="Союзник", might=50, pact_id=7
    )
    engine = _raid_night_engine(
        fiefs={1: atk, 2: vic_a, 3: ally, 4: vic_b},
        pact_members=[vic_a, ally, vic_b],
        watch_defense=1.0,
    )
    engine.db.pact_members.side_effect = lambda pid: [
        engine._fiefs[2],
        engine._fiefs[3],
        engine._fiefs[4],
    ]
    engine.raid_declare_is_open = MagicMock(return_value=True)
    engine._world_id_for_realm = MagicMock(return_value=1)
    engine._format_raid_deadline = MagicMock(return_value="12:00")
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine.collect_for_fief = MagicMock()
    for realm in engine._realms.values():
        realm["day_number"] = 5
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    _inject_raid_intent(
        engine, fief_id=1, victim_id=2, might=3, status="locked"
    )
    atk2 = _base_fief(
        5, realm_id=1, user_id=505, name="Атакующий2", might=40
    )
    engine._fiefs[5] = atk2
    _inject_raid_intent(
        engine, fief_id=5, victim_id=4, might=3, status="locked"
    )

    reinforce_by_victim: dict[int, int] = {}

    def _spy_resolve_raid(**kwargs):
        name = kwargs.get("victim_name") or ""
        if "ЖертваА" in name:
            reinforce_by_victim[2] = int(kwargs.get("reinforce_might") or 0)
        if "ЖертваБ" in name:
            reinforce_by_victim[4] = int(kwargs.get("reinforce_might") or 0)
        return resolve_raid(**kwargs)

    with patch(
        "app.services.night_raids.resolve_raid",
        side_effect=_spy_resolve_raid,
    ):
        engine.resolve_pending_raids(1, 10)

    # Без потолка суммы первый набег забирает весь бюджет; второй видит 0.
    assert reinforce_by_victim.get(2) == 20
    assert reinforce_by_victim.get(4) == 0


def test_guide_mentions_zastava_and_no_declare_ping():
    text = game_guide()
    assert "Застава" in text
    assert "нет / мало / средне / много" not in text
    assert "политик" in text.lower()
    assert "сюрприз" in text.lower() or "не пишет" in text
    assert "авто-перехват" in text or "INTERCEPT" not in text
