"""UI передачи ресурсов: контакты, пресеты, chrome."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.caravans import CaravanService
from app.ui.keyboards.chrome import menu_only_kb, pending_escape_row
from app.ui.keyboards.transfer import (
    transfer_amount_presets,
    transfer_cancel_intent_kb,
    transfer_contacts_kb,
    transfer_resource_kb,
)


def test_transfer_contacts_kb_has_find_cancel_menu():
    kb = transfer_contacts_kb(3, [(7, "Бета"), (8, "Кирилл")])
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert data[:2] == ["snd:3:t:7", "snd:3:t:8"]
    assert "snd:3:find" in data
    assert "pend:cancel:3" in data
    assert "home:3" in data


def test_transfer_resource_kb_shows_balances():
    kb = transfer_resource_kb(4, grain=12, goods=5)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "Зерно (12)" in texts
    assert "Товары (5)" in texts
    assert "snd:4:r:grain" in data
    assert "pend:cancel:4" in data
    assert "home:4" in data


def test_transfer_amount_presets_include_public_threshold_and_all():
    assert transfer_amount_presets(0) == []
    assert 30 in transfer_amount_presets(50)
    assert transfer_amount_presets(7)[-1] == 7


def test_transfer_cancel_intent_has_menu():
    kb = transfer_cancel_intent_kb(9, 21)
    assert kb.inline_keyboard[0][0].text == "Отменить отправку"
    assert kb.inline_keyboard[0][0].callback_data == "cvx:9:21"
    assert kb.inline_keyboard[-1][0].callback_data == "home:9"


def test_menu_only_and_pending_escape():
    assert menu_only_kb(1).inline_keyboard[0][0].callback_data == "home:1"
    row = pending_escape_row(2)
    assert [b.callback_data for b in row] == ["pend:cancel:2", "home:2"]


def test_list_transfer_contacts_recent_then_pact():
    db = MagicMock()
    engine = MagicMock()
    engine.fief_label.side_effect = lambda f: f["name"]

    sender = {
        "id": 1,
        "user_id": 100,
        "realm_id": 10,
        "pact_id": 5,
        "frozen": False,
        "name": "Я",
    }
    recent = {
        "id": 2,
        "user_id": 200,
        "realm_id": 10,
        "frozen": False,
        "name": "Недавний",
    }
    ally = {
        "id": 3,
        "user_id": 300,
        "realm_id": 11,
        "frozen": False,
        "name": "Союзник",
    }
    fiefs = {1: sender, 2: recent, 3: ally}
    db.get_fief.side_effect = lambda fid: fiefs.get(int(fid))
    db.list_recent_caravan_receiver_ids.return_value = [2]
    db.pact_members.return_value = [ally]
    db.realms_are_adjacent.return_value = True

    svc = CaravanService(engine, db)
    contacts = svc.list_transfer_contacts(1, limit=8)
    assert contacts == [(2, "Недавний"), (3, "Союзник")]
