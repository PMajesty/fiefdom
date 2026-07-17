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
        "good_stone",
        "drought",
        "wedding",
        "omen",
        "blight",
        "press_gang",
        "fire",
        "toll",
        "spoilage",
    }
    assert set(events.MINOR_EVENTS) == expected
    for key, row in events.MINOR_EVENTS.items():
        assert row["id"] == key
        assert row["name_ru"]
        assert row["digest_line"]
        assert row["canned_narrative"]
        assert "mechanics" in row
        assert isinstance(events.minor_effect(key), dict)


def test_event_digest_line_never_leaks_mechanics():
    for key, row in events.MINOR_EVENTS.items():
        line = events.event_digest_line(row)
        assert line
        assert "_" not in line
        assert "farm_mult" not in line
        assert "trade_bonus" not in line
        assert row["mechanics"] not in line
        assert line == row["digest_line"]


def test_event_digest_line_fallback_without_digest_field():
    meta = {"name_ru": "Тест", "mechanics": "farm_mult+1"}
    assert events.event_digest_line(meta) == "Тест"
    assert "farm_mult" not in events.event_digest_line(meta)


def test_format_lots_count_pluralization():
    assert digest.format_lots_count(1) == "1 лот"
    assert digest.format_lots_count(2) == "2 лота"
    assert digest.format_lots_count(3) == "3 лота"
    assert digest.format_lots_count(4) == "4 лота"
    assert digest.format_lots_count(5) == "5 лотов"
    assert digest.format_lots_count(11) == "11 лотов"
    assert digest.format_lots_count(21) == "21 лот"
    assert digest.format_lots_count(22) == "22 лота"
    assert digest.ru_plural(0, "лот", "лота", "лотов") == "лотов"


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


def test_shipped_minor_keys_documented_and_subset():
    assert events.SHIPPED_MINOR_KEYS == frozenset(
        {
            "harvest",
            "fog",
            "rats",
            "fair",
            "good_stone",
            "drought",
            "wedding",
            "omen",
            "blight",
            "press_gang",
            "fire",
            "toll",
            "spoilage",
        }
    )
    assert events.SHIPPED_MINOR_KEYS <= set(events.MINOR_EVENTS)
    gated = set(events.MINOR_EVENTS) - events.SHIPPED_MINOR_KEYS
    assert gated == {"trader"}
    assert set(events.MINOR_EVENT_WEIGHTS) >= events.SHIPPED_MINOR_KEYS


def test_shipped_catastrophe_keys_documented_and_subset():
    assert events.SHIPPED_CATASTROPHE_KEYS == frozenset(
        {"bandit_night", "cattle_plague"}
    )
    assert events.SHIPPED_CATASTROPHE_KEYS <= set(events.CATASTROPHES)
    gated = set(events.CATASTROPHES) - events.SHIPPED_CATASTROPHE_KEYS
    assert gated == {
        "flood",
        "rat_king",
        "dragon_rumors",
        "black_fair",
    }


def test_gated_content_remains_in_tables():
    assert "trader" in events.MINOR_EVENTS
    assert isinstance(events.minor_effect("trader"), dict)
    for key in ("drought", "blight", "fire"):
        assert key in events.MINOR_EVENTS
        assert key in events.SHIPPED_MINOR_KEYS
        assert isinstance(events.minor_effect(key), dict)
    assert "deserter" not in events.MINOR_EVENTS
    for key in ("flood", "cattle_plague", "rat_king", "dragon_rumors", "black_fair"):
        assert key in events.CATASTROPHES
        assert isinstance(events.catastrophe_effect(key), dict)
    assert "cattle_plague" in events.SHIPPED_CATASTROPHE_KEYS


def test_roll_minor_event_every_tick():
    assert B.MINOR_EVENT_CHANCE == 1.0
    for seed in range(200):
        result = events.roll_minor_event(Random(seed))
        assert result is not None
        assert result in events.SHIPPED_MINOR_KEYS
        assert result in events.MINOR_EVENTS


def test_bad_event_effects_are_harsher():
    assert events.minor_effect("rats")["loss_frac"] == 0.25
    assert events.minor_effect("blight")["goods_loss_frac"] == 0.225
    assert events.minor_effect("spoilage")["grain_loss_frac"] == 0.1875
    assert events.minor_effect("drought")["farm_mult"] == 0.4375
    assert events.minor_effect("toll")["goods_flat_loss"] == 15
    assert events.minor_effect("press_gang")["might_loss"] == 4
    assert events.catastrophe_effect("cattle_plague")["farm_mult"] == 0.375
    assert B.BANDIT_NIGHT_FAIL_GRAIN_FRAC == 0.3125


def test_roll_minor_event_never_returns_gated_keys():
    gated = set(events.MINOR_EVENTS) - events.SHIPPED_MINOR_KEYS
    for seed in range(500):
        result = events.roll_minor_event(Random(seed))
        assert result is not None
        assert result not in gated
        assert result in events.SHIPPED_MINOR_KEYS


def test_pick_catastrophe_only_shipped():
    rng = Random(42)
    last = None
    for _ in range(80):
        picked = events.pick_catastrophe(rng, last)
        assert picked in events.SHIPPED_CATASTROPHE_KEYS
        assert picked in events.CATASTROPHES
        last = picked


def test_pick_catastrophe_never_returns_gated_keys():
    gated = set(events.CATASTROPHES) - events.SHIPPED_CATASTROPHE_KEYS
    for seed in range(200):
        picked = events.pick_catastrophe(Random(seed), "bandit_night")
        assert picked not in gated
        assert picked in events.SHIPPED_CATASTROPHE_KEYS


def test_pick_catastrophe_avoids_last_when_possible():
    shipped = sorted(events.SHIPPED_CATASTROPHE_KEYS)
    if len(shipped) < 2:
        # один ключ в пуле - повтор last допустим
        for seed in range(20):
            assert events.pick_catastrophe(Random(seed), shipped[0]) == shipped[0]
        return
    rng = Random(42)
    last = shipped[0]
    for _ in range(50):
        picked = events.pick_catastrophe(rng, last)
        assert picked != last
        assert picked in events.SHIPPED_CATASTROPHE_KEYS
        last = picked


def test_pick_catastrophe_allows_shipped_when_last_none():
    assert events.pick_catastrophe(Random(1), None) in events.SHIPPED_CATASTROPHE_KEYS


def test_next_catastrophe_delay_ticks_range():
    for seed in range(40):
        delay = events.next_catastrophe_delay_ticks(Random(seed))
        assert B.CATASTROPHE_MIN_TICKS <= delay <= B.CATASTROPHE_MAX_TICKS


def test_minor_effect_unknown():
    with pytest.raises(KeyError):
        events.minor_effect("shrine")


def test_format_digest_gdd_shape():
    text = digest.format_digest(
        realm_title="Долина друзей",
        day=43,
        night_lines=[
            "Саша ограбил Кирилла.",
            "Набег Оли на Иру отбит.",
        ],
        event_line="Засуха - урожай слабее.",
        feud_lines=["Саша против Кирилла - неделя вторая."],
        sunday_extra=None,
    )
    assert text.startswith("🏰 Долина друзей - день 43")
    assert "🌙 Ночью: Саша ограбил Кирилла. Набег Оли на Иру отбит." in text
    assert "📜 Сегодня: Засуха - урожай слабее." in text
    assert "⚔️ Вражда: Саша против Кирилла - неделя вторая." in text
    assert "\n\n🌙 Ночью:" in text
    assert "\n\n📜 Сегодня:" in text
    assert "\n\n⚔️ Вражда:" in text
    assert "Вражда: Вражда:" not in text
    assert "🛒" not in text
    assert "farm_mult" not in text


def test_format_digest_feud_single_prefix():
    text = digest.format_digest(
        realm_title="Тест",
        day=2,
        night_lines=[],
        event_line=None,
        feud_lines=["Саша против Кирилла"],
        sunday_extra=None,
    )
    assert "⚔️ Вражда: Саша против Кирилла" in text
    assert text.count("Вражда:") == 1


def test_format_digest_quiet_night_and_sunday():
    text = digest.format_digest(
        realm_title="Тест",
        day=1,
        night_lines=[],
        event_line=None,
        feud_lines=[],
        sunday_extra="🏅 Титулы: Хлебный барон - Ваня.",
    )
    assert "🌙 Ночью: тихо." in text
    assert "📜" not in text
    assert "🛒" not in text
    assert "⚔️" not in text
    assert "🏅 Титулы: Хлебный барон - Ваня." in text


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
    assert absence.inactivity_tier(B.DORMANT_TICKS - 1) == "ok"
    assert absence.inactivity_tier(B.DORMANT_TICKS) == "dormant"
    assert absence.inactivity_tier(B.OVERGROWN_TICKS - 1) == "dormant"
    assert absence.inactivity_tier(B.OVERGROWN_TICKS) == "overgrown"


def test_compensation_for_claim():
    assert absence.compensation_for_claim(120) == 60
    assert absence.compensation_for_claim(0) == 0
