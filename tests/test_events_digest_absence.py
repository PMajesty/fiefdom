"""Тесты событий, сводки и отсутствия."""
from __future__ import annotations

from random import Random

import pytest

from app import balance as B
from app.domain import absence, digest, events


def test_minor_events_table_complete():
    expected = {
        "harvest",
        "fog",
        "trader",
        "rats",
        "fair",
        "deserter",
        "good_stone",
        "drought",
        "wedding",
        "omen",
    }
    assert set(events.MINOR_EVENTS) == expected
    for key, row in events.MINOR_EVENTS.items():
        assert row["id"] == key
        assert row["name_ru"]
        assert row["canned_narrative"]
        assert "mechanics" in row
        assert isinstance(events.minor_effect(key), dict)


def test_catastrophes_table_complete():
    expected = {
        "bandit_night",
        "flood",
        "cattle_plague",
        "rat_king",
        "dragon_rumors",
        "black_fair",
    }
    assert set(events.CATASTROPHES) == expected
    for key, row in events.CATASTROPHES.items():
        assert row["id"] == key
        assert row["name_ru"]
        assert row["canned_narrative"]
        assert isinstance(events.catastrophe_effect(key), dict)


def test_roll_minor_event_quiet_and_hit():
    quiet = events.roll_minor_event(Random(0))
    # seed 0: first random() is below chance on CPython — verify distribution instead
    hits = 0
    none_count = 0
    for seed in range(200):
        result = events.roll_minor_event(Random(seed))
        if result is None:
            none_count += 1
        else:
            hits += 1
            assert result in events.MINOR_EVENTS
    assert hits > 0 and none_count > 0
    assert abs(hits / 200 - B.MINOR_EVENT_CHANCE) < 0.15
    assert quiet is None or quiet in events.MINOR_EVENTS


def test_pick_catastrophe_avoids_last():
    rng = Random(42)
    last = "flood"
    for _ in range(50):
        picked = events.pick_catastrophe(rng, last)
        assert picked != last
        assert picked in events.CATASTROPHES
        last = picked


def test_pick_catastrophe_allows_any_when_last_none():
    assert events.pick_catastrophe(Random(1), None) in events.CATASTROPHES


def test_next_catastrophe_delay_days_range():
    for seed in range(40):
        delay = events.next_catastrophe_delay_days(Random(seed))
        assert B.CATASTROPHE_MIN_DAYS <= delay <= B.CATASTROPHE_MAX_DAYS


def test_minor_effect_unknown():
    with pytest.raises(KeyError):
        events.minor_effect("shrine")


def test_format_digest_gdd_shape():
    text = digest.format_digest(
        realm_title="Долина друзей",
        day=43,
        night_lines=[
            "Саша ограбил Кирилла (−34 товара).",
            "Набег Оли на Иру отбит.",
        ],
        event_line="Засуха — фермы −30%. Полив: 10 товаров (в личке).",
        market_line="3 лота. Лучший: 40 зерна за 25 товаров (Ваня).",
        feud_lines=["Саша против Кирилла — неделя вторая."],
        sunday_extra=None,
    )
    assert text.startswith("🏰 Долина друзей — день 43")
    assert "🌙 Ночью: Саша ограбил Кирилла (−34 товара). Набег Оли на Иру отбит." in text
    assert "📜 Сегодня: Засуха — фермы −30%. Полив: 10 товаров (в личке)." in text
    assert "🛒 Рынок: 3 лота. Лучший: 40 зерна за 25 товаров (Ваня)." in text
    assert "⚔️ Вражда: Саша против Кирилла — неделя вторая." in text


def test_format_digest_quiet_night_and_sunday():
    text = digest.format_digest(
        realm_title="Тест",
        day=1,
        night_lines=[],
        event_line=None,
        market_line=None,
        feud_lines=[],
        sunday_extra="🏅 Титулы: Хлебный барон — Ваня.",
    )
    assert "🌙 Ночью: тихо." in text
    assert "📜" not in text
    assert "🛒" not in text
    assert "⚔️" not in text
    assert "🏅 Титулы: Хлебный барон — Ваня." in text


def test_format_decree():
    text = digest.format_decree(
        7,
        "Отныне амбары укрывают больше зерна (40% → 45% на II уровне).\nКрысы недовольны.",
    )
    assert text == (
        "📜 УКАЗ №7\n"
        "Отныне амбары укрывают больше зерна (40% → 45% на II уровне).\n"
        "Крысы недовольны."
    )


def test_inactivity_tiers():
    assert absence.inactivity_tier(0) == "ok"
    assert absence.inactivity_tier(B.DORMANT_DAYS - 1) == "ok"
    assert absence.inactivity_tier(B.DORMANT_DAYS) == "dormant"
    assert absence.inactivity_tier(B.OVERGROWN_DAYS - 1) == "dormant"
    assert absence.inactivity_tier(B.OVERGROWN_DAYS) == "overgrown"


def test_compensation_for_claim():
    assert absence.compensation_for_claim(120) == 60
    assert absence.compensation_for_claim(0) == 0
