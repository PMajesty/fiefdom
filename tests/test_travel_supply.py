"""Снабжение похода: fee math, raid/cover charge, refunds, sunk post-lock."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app import balance as B
from app.domain.cover import COVER_MODE_ANY, COVER_MODE_SPECIFIC
from app.domain.guide import game_guide
from app.domain.patch_notes import PATCH_NOTES
from app.domain.travel_supply import (
    format_travel_supply_charge_line,
    intent_supply_grain,
    travel_supply_net_delta,
)
from app.presenters.status import StatusSnapshot, render_status_card
from tests.test_cover_stances import (
    _cover_engine,
    _leave_pact_cover_engine,
)
from tests.test_raid_night_characterization import _inject_raid_intent


def test_travel_supply_grain_math_no_free_band():
    assert B.travel_supply_grain(0) == 0
    assert B.travel_supply_grain(5) == 3
    assert B.travel_supply_grain(10) == 5
    assert B.travel_supply_grain(20) == 10
    assert B.travel_supply_grain(1) == 1
    assert B.travel_supply_grain(5) != B.militia_upkeep_grain(5)


def test_travel_supply_net_delta():
    assert travel_supply_net_delta(prior_fee=5, new_fee=5) == 0
    assert travel_supply_net_delta(prior_fee=5, new_fee=10) == 5
    assert travel_supply_net_delta(prior_fee=10, new_fee=5) == -5
    assert intent_supply_grain({}) == 0
    assert intent_supply_grain({"supply_grain": 7}) == 7


def test_raid_declare_charges_supply_and_cancel_refunds_while_open():
    engine = _cover_engine()
    engine._spend_action = MagicMock()
    engine._require_cross_valley_caught_up = MagicMock()
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine._fiefs[1]["onboard_step"] = 4
    engine._fiefs[1]["grain"] = 40
    engine._fiefs[1]["might"] = 40
    engine._realms[1]["day_number"] = 5
    engine.db.get_realm.side_effect = lambda rid: engine._realms.get(int(rid))

    result = engine.declare_raid(1, 2, 10)
    fee = B.travel_supply_grain(10)
    assert fee == 5
    assert engine._fiefs[1]["might"] == 30
    assert engine._fiefs[1]["grain"] == 35
    intent = next(i for i in engine._intents if i["kind"] == "raid")
    assert intent["payload"]["supply_grain"] == fee
    assert "снабжение" in result.dm_text.lower()

    msg = engine.cancel_raid_intent(1, intent["id"])
    assert engine._fiefs[1]["might"] == 40
    assert engine._fiefs[1]["grain"] == 40
    assert "зерна" in msg.lower() or "снабжен" in msg.lower()


def test_raid_declare_blocks_insufficient_grain_and_hungry():
    engine = _cover_engine()
    engine._spend_action = MagicMock()
    engine._require_cross_valley_caught_up = MagicMock()
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine._fiefs[1]["onboard_step"] = 4
    engine._realms[1]["day_number"] = 5
    engine.db.get_realm.side_effect = lambda rid: engine._realms.get(int(rid))

    engine._fiefs[1]["grain"] = 2
    with pytest.raises(ValueError, match="зерна"):
        engine.declare_raid(1, 2, 10)
    assert engine._fiefs[1]["might"] == 40
    assert engine._fiefs[1]["grain"] == 2

    engine._fiefs[1]["grain"] = 40
    engine._fiefs[1]["hungry"] = True
    with pytest.raises(ValueError, match="Голодные"):
        engine.declare_raid(1, 2, 10)


def test_cover_declare_charges_and_hungry_blocks():
    engine = _cover_engine()
    grain_before = int(engine._fiefs[3]["grain"])
    msg = engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee = B.travel_supply_grain(10)
    assert engine._fiefs[3]["might"] == 30
    assert engine._fiefs[3]["grain"] == grain_before - fee
    intent = next(i for i in engine._intents if i["kind"] == "cover_stance")
    assert intent["payload"]["supply_grain"] == fee
    assert "снабжение" in msg.lower()

    engine._fiefs[3]["hungry"] = True
    with pytest.raises(ValueError, match="Голодные"):
        engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)


def test_cover_insufficient_grain_blocks():
    engine = _cover_engine()
    engine._fiefs[3]["grain"] = 2
    with pytest.raises(ValueError, match="зерна"):
        engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    assert engine._fiefs[3]["might"] == 40
    assert not any(i["kind"] == "cover_stance" for i in engine._intents)


def test_cover_pre_lock_cancel_and_stand_down_refund_supply():
    engine = _cover_engine()
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee = B.travel_supply_grain(10)
    intent = next(i for i in engine._intents if i["kind"] == "cover_stance")
    msg = engine.cancel_cover_stance_intent(3, intent["id"])
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0
    assert str(fee) in msg

    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=8)
    fee8 = B.travel_supply_grain(8)
    assert engine._fiefs[3]["grain"] == grain0 - fee8
    engine.set_cover_stand_down(3)
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0


def test_cover_same_budget_retarget_no_second_charge():
    engine = _cover_engine()
    other = engine._fiefs[2]
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee = B.travel_supply_grain(10)
    assert engine._fiefs[3]["grain"] == grain0 - fee
    msg = engine.set_cover_stance(
        3, mode=COVER_MODE_SPECIFIC, budget=10, target_fief_id=int(other["id"])
    )
    assert engine._fiefs[3]["grain"] == grain0 - fee
    assert engine._fiefs[3]["might"] == 30
    assert "без доплаты" in msg.lower()
    open_intents = [
        i
        for i in engine._intents
        if i["kind"] == "cover_stance" and i["status"] == "open"
    ]
    assert len(open_intents) == 1
    assert open_intents[0]["payload"]["supply_grain"] == fee
    assert open_intents[0]["payload"]["mode"] == COVER_MODE_SPECIFIC


def test_cover_confirm_copy_accounts_for_open_escrow():
    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee10 = B.travel_supply_grain(10)
    fee20 = B.travel_supply_grain(20)
    prior_budget, prior_supply = engine.open_cover_stance_escrow_preview(3)
    assert prior_budget == 10
    assert prior_supply == fee10
    yard = int(engine._fiefs[3]["might"])
    men_home_same = yard + prior_budget - 10
    men_home_up = yard + prior_budget - 20
    assert men_home_same == 30
    assert men_home_up == 20
    same_line = format_travel_supply_charge_line(new_fee=fee10, prior_fee=fee10)
    up_line = format_travel_supply_charge_line(new_fee=fee20, prior_fee=fee10)
    assert "без доплаты" in same_line.lower()
    assert "доплата" in up_line.lower()
    assert str(fee20 - fee10) in up_line


@pytest.mark.asyncio
async def test_cover_budget_pending_confirm_uses_open_escrow_preview():
    from unittest.mock import AsyncMock, patch

    from app.handlers.dm import _handle_pending

    engine = _cover_engine()
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee10 = B.travel_supply_grain(10)
    message = MagicMock()
    message.from_user = MagicMock(id=303)
    pending = {
        "kind": "cover_budget",
        "fief_id": 3,
        "mode": "any",
    }
    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply,
        patch("app.handlers.dm.set_pending") as set_pending,
        patch("app.handlers.dm.cover_confirm_kb", return_value="kb"),
    ):
        ok = await _handle_pending(message, engine, pending, "10")
    assert ok is True
    set_pending.assert_called_once()
    text = reply.await_args.args[1]
    assert "дома останется 30" in text
    assert "без доплаты" in text.lower()
    assert str(fee10) in text

    with (
        patch("app.handlers.dm.reply_game", new_callable=AsyncMock) as reply2,
        patch("app.handlers.dm.set_pending"),
        patch("app.handlers.dm.cover_confirm_kb", return_value="kb"),
    ):
        ok2 = await _handle_pending(message, engine, pending, "20")
    assert ok2 is True
    text2 = reply2.await_args.args[1]
    assert "дома останется 20" in text2
    assert "доплата" in text2.lower()


def test_cover_budget_change_nets_supply():
    engine = _cover_engine()
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    fee10 = B.travel_supply_grain(10)
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    fee20 = B.travel_supply_grain(20)
    assert engine._fiefs[3]["grain"] == grain0 - fee20
    assert fee20 - fee10 == 5
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=10)
    assert engine._fiefs[3]["grain"] == grain0 - fee10


def test_leave_pact_open_refunds_supply_dissolve_open_too():
    engine = _leave_pact_cover_engine()
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    fee = B.travel_supply_grain(12)
    engine.leave_pact(3)
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0

    # Заново: роспуск в первой половине (open) возвращает зерно.
    engine._fiefs[3]["pact_id"] = 7
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    engine.db.pact_members.side_effect = lambda pid: [
        f for f in (engine._fiefs[2], engine._fiefs[3]) if f.get("pact_id") == 7
    ]
    msg = engine.leave_pact(3)
    assert "распущен" in msg.lower()
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0
    assert fee == B.travel_supply_grain(12)


def test_dissolve_after_lock_refunds_might_not_supply():
    engine = _leave_pact_cover_engine()
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=12)
    fee = B.travel_supply_grain(12)
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"
    engine.leave_pact(3)
    assert engine._fiefs[3]["grain"] == grain0 - fee
    engine.db.pact_members.side_effect = lambda pid: [
        f for f in (engine._fiefs[2], engine._fiefs[4]) if f.get("pact_id") == 7
    ]
    engine.leave_pact(4)
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0 - fee


def test_post_lock_quiet_night_trim_and_deaths_keep_supply_sunk():
    engine = _cover_engine()
    grain0 = int(engine._fiefs[3]["grain"])
    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    fee = B.travel_supply_grain(20)
    assert engine._fiefs[3]["grain"] == grain0 - fee
    for i in engine._intents:
        if i["kind"] == "cover_stance":
            i["status"] = "locked"

    notices = engine.resolve_remaining_cover_stances(1, 10)
    assert engine._fiefs[3]["might"] == 40
    assert engine._fiefs[3]["grain"] == grain0 - fee
    assert notices

    engine.set_cover_stance(3, mode=COVER_MODE_ANY, budget=20)
    cover_intent = next(
        i
        for i in engine._intents
        if i["kind"] == "cover_stance" and i["status"] == "open"
    )
    cover_intent["status"] = "locked"
    grain_after = int(engine._fiefs[3]["grain"])
    _inject_raid_intent(engine, fief_id=1, victim_id=2, might=5, status="locked")
    engine.resolve_pending_raids(1, 10)
    assert engine._fiefs[3]["grain"] == grain_after


def test_raid_night_returns_might_keeps_supply_sunk():
    engine = _cover_engine()
    engine._spend_action = MagicMock()
    engine._require_cross_valley_caught_up = MagicMock()
    engine.require_active_fief = MagicMock(
        side_effect=lambda fid: engine.db.get_fief(fid)
    )
    engine._fiefs[1]["onboard_step"] = 4
    engine._fiefs[1]["grain"] = 40
    engine._fiefs[1]["might"] = 40
    # Пустой двор жертвы: без добычи, чтобы не спутать с возвратом снабжения.
    engine._fiefs[2]["grain"] = 0
    engine._fiefs[2]["goods"] = 0
    engine._realms[1]["day_number"] = 5
    engine.db.get_realm.side_effect = lambda rid: engine._realms.get(int(rid))
    engine.declare_raid(1, 2, 10)
    fee = B.travel_supply_grain(10)
    grain_locked = 40 - fee
    assert engine._fiefs[1]["grain"] == grain_locked
    intent = next(i for i in engine._intents if i["kind"] == "raid")
    intent["status"] = "locked"
    intent["payload"].update(
        {
            "road_planned": True,
            "road_deaths": 0,
            "fled": False,
            "siege_eligible": True,
            "road_public_line": "",
            "returned_might": 10,
        }
    )
    engine.resolve_pending_raids(1, 10)
    assert engine._fiefs[1]["grain"] == grain_locked
    assert engine._fiefs[1]["might"] >= 30
    assert intent_supply_grain(intent.get("payload")) == fee


def test_guide_and_patch_note_document_road_supply():
    text = game_guide()
    assert "снабжение похода" in text.lower()
    assert "дружина дома" in text.lower() or "только для тех, кто дома" in text
    note = next(n for n in PATCH_NOTES if n.id == "road_supply_fee_v1")
    body = " ".join(note.body_lines).lower()
    assert "снабжение" in body
    assert "глашатай" not in note.title.lower()


def test_status_card_says_home_militia():
    card = render_status_card(
        StatusSnapshot(
            fief_label="Хутор",
            day_number=1,
            alerts=(),
            actions=1,
            actions_max=3,
            tile_count=1,
            tile_cap=9,
            stash_line="Склад",
            barn_line="Амбар",
            production_line="Доход",
            land_upkeep=4,
            militia_upkeep=2,
            next_tick_line="Тик",
            prep_lines=(),
            notes=(),
        )
    )
    assert "дружина дома 2" in card
