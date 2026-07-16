"""Тесты слухов долины: бенды, ложь, сводка, pull."""
from __future__ import annotations

from random import Random

from app import balance as B
from app.domain import digest, rumors


def _snap(**kwargs) -> rumors.FiefRumorSnapshot:
    base = dict(
        fief_id=1,
        name="Иван",
        grain=50,
        goods=50,
        might=10,
        buildings=((B.BLD_FARM, 1),),
        patrol_active=False,
    )
    base.update(kwargs)
    return rumors.FiefRumorSnapshot(**base)


def test_wealth_and_might_bands():
    assert rumors.wealth_band(0) == 0
    assert rumors.wealth_band(39) == 0
    assert rumors.wealth_band(40) == 1
    assert rumors.wealth_band(119) == 1
    assert rumors.wealth_band(120) == 2
    assert rumors.wealth_band(299) == 2
    assert rumors.wealth_band(300) == 3
    assert rumors.might_band(0) == 0
    assert rumors.might_band(7) == 0
    assert rumors.might_band(8) == 1
    assert rumors.might_band(19) == 1
    assert rumors.might_band(20) == 2


def test_compose_full_wealth_uses_true_band():
    snap = _snap(grain=10, goods=5)  # total 15 -> band 0
    text = rumors.compose_rumor_text(snap, rumors.FACT_WEALTH, rumors.TRUTH_FULL, Random(1))
    assert "Иван" in text
    assert "тощая" in text


def test_compose_false_wealth_changes_band():
    snap = _snap(grain=10, goods=5)
    text = rumors.compose_rumor_text(snap, rumors.FACT_WEALTH, rumors.TRUTH_FALSE, Random(0))
    assert "Иван" in text
    assert "тощая" not in text


def test_compose_false_patrol_flips():
    snap = _snap(patrol_active=True)
    text = rumors.compose_rumor_text(snap, rumors.FACT_PATROL, rumors.TRUTH_FALSE, Random(0))
    assert "без дозора" in text or "без лишнего" in text


def test_compose_building_names_russian():
    snap = _snap(buildings=((B.BLD_WATCH, 2),))
    text = rumors.compose_rumor_text(snap, rumors.FACT_BUILDING, rumors.TRUTH_FULL, Random(2))
    assert "сторожка" in text.lower()
    assert "II" in text


def test_roll_daily_rumors_bounded_and_seeded():
    fiefs = [
        _snap(fief_id=1, name="А"),
        _snap(fief_id=2, name="Б", grain=200, goods=200, might=30),
    ]
    lines = rumors.roll_daily_rumors(fiefs, Random(42))
    assert 0 <= len(lines) <= B.RUMOR_MAX_PER_DAY
    for line in lines:
        assert line.endswith(".")
        assert "говорят" in line or "шепчут" in line


def test_roll_daily_rumors_empty_realm():
    assert rumors.roll_daily_rumors([], Random(1)) == []


def test_format_rumor_section_has_disclaimer():
    section = rumors.format_rumor_section(["У Ивана, говорят, дружина тонка."])
    assert section is not None
    assert "не факты" in section
    assert "базар может врать" in section
    assert "• У Ивана" in section


def test_format_rumors_pull_empty_explains():
    text = rumors.format_rumors_pull([])
    assert "не разведка" in text
    assert "молчит" in text


def test_format_digest_includes_rumor_section():
    text = digest.format_digest(
        realm_title="Долина",
        day=5,
        night_lines=[],
        event_line=None,
        market_line=None,
        feud_lines=[],
        sunday_extra=None,
        rumor_lines=["У Кирилла, говорят, амбар ломится."],
    )
    assert "👂 Слухи (не факты - базар может врать):" in text
    assert "• У Кирилла, говорят, амбар ломится." in text
    assert text.index("Слухи") < text.index("Кирилла")


def test_format_digest_omits_rumors_when_empty():
    text = digest.format_digest(
        realm_title="Долина",
        day=5,
        night_lines=[],
        event_line=None,
        market_line=None,
        feud_lines=[],
        sunday_extra="🏅 Титулы: тест.",
        rumor_lines=[],
    )
    assert "Слухи" not in text
    assert "🏅 Титулы: тест." in text


def test_truth_weights_cover_falsehood():
    assert 0 < B.RUMOR_TRUTH_FULL < 1
    assert 0 < B.RUMOR_TRUTH_FUZZY < 1
    assert B.RUMOR_TRUTH_FULL + B.RUMOR_TRUTH_FUZZY < 1
    false_rate = 1.0 - B.RUMOR_TRUTH_FULL - B.RUMOR_TRUTH_FUZZY
    assert 0.05 <= false_rate <= 0.30
