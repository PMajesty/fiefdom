"""Characterization: ui.flows (text, keyboard) offer builders."""
from __future__ import annotations

from app import balance as B
from app.ui.flows import (
    claim_offer,
    pact_menu_offer,
    raid_targets_offer,
    send_find_offer,
    send_offer,
)


def test_claim_offer_empty_returns_text_with_menu():
    text, kb = claim_offer(
        3,
        [],
        next_tile_count=2,
        tile_meta={},
        empty_text="Нет клеток для занятия.",
        prompt_text="Выберите клетку:",
    )
    assert text == "Нет клеток для занятия."
    assert kb is not None
    assert kb.inline_keyboard[-1][0].callback_data == "home:3"


def test_claim_offer_prompt_passthrough_with_keyboard():
    text, kb = claim_offer(
        3,
        [(0, 2)],
        next_tile_count=2,
        tile_meta={(0, 2): (B.TILE_FIELD, False)},
        empty_text="empty",
        prompt_text="Выберите клетку для занятия:",
    )
    assert text == "Выберите клетку для занятия:"
    assert kb is not None
    assert kb.inline_keyboard[0][0].callback_data == "clm:3:0:2"
    assert kb.inline_keyboard[-1][0].callback_data == "home:3"


def test_raid_targets_offer_empty_and_content():
    empty_text, empty_kb = raid_targets_offer(
        1,
        [],
        empty_text="Некого грабить.",
        prompt_text="ignored",
    )
    assert empty_text == "Некого грабить."
    assert empty_kb is not None
    assert empty_kb.inline_keyboard[-1][0].callback_data == "home:1"

    prompt = (
        "Выберите цель набега (любая долина континента).\n"
        "Точная сила скрыта - смотрите слухи или спрашивайте. "
        "Защита цели - дружина на месте, сторожка, дозор и перехват пакта."
    )
    text, kb = raid_targets_offer(
        1,
        [{"id": 2, "label": "Сосед", "might": 17}],
        empty_text="Некого грабить.",
        prompt_text=prompt,
    )
    assert text == prompt
    assert kb is not None
    assert kb.inline_keyboard[0][0].callback_data == "rad:1:2"


def test_send_offer_contacts_and_find():
    text, kb = send_offer(9, [(2, "Бета")])
    assert "Кому отправить" in text
    assert "Найти" in text
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "snd:9:t:2" in data
    assert "snd:9:find" in data
    assert "pend:cancel:9" in data
    assert "home:9" in data


def test_send_find_offer_asks_for_text():
    text, kb = send_find_offer(9)
    assert "id усадьбы" in text
    assert kb.inline_keyboard[-1][0].text == "Отмена"
    assert kb.inline_keyboard[-1][1].callback_data == "home:9"


def test_pact_menu_offer_preserves_text():
    text, kb = pact_menu_offer(
        4,
        in_pact=False,
        is_founder=False,
        text="Вы не в пакте.",
    )
    assert text == "Вы не в пакте."
    assert kb is not None
    assert any(
        btn.callback_data == "pct:new:4"
        for row in kb.inline_keyboard
        for btn in row
    )
