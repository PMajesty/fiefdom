"""Тесты слухов долины: бенды, ложь, сводка, pull, чужие долины."""
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
        realm_title="",
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


def test_might_soft_label_hides_number():
    assert rumors.might_soft_label(0) == "тонкая"
    assert rumors.might_soft_label(7) == "тонкая"
    assert rumors.might_soft_label(8) == "крепкая"
    assert rumors.might_soft_label(19) == "крепкая"
    assert rumors.might_soft_label(20) == "толпа"
    assert rumors.might_soft_label(99) == "толпа"


def test_rumor_local_max_scales_with_players():
    assert rumors.rumor_local_max_lines(0) == 0
    assert rumors.rumor_local_max_lines(1) == B.RUMOR_MAX_PER_DAY
    assert rumors.rumor_local_max_lines(2) == B.RUMOR_MAX_PER_DAY
    assert rumors.rumor_local_max_lines(3) == B.RUMOR_MAX_PER_DAY + 1
    assert rumors.rumor_local_max_lines(5) == B.RUMOR_MAX_PER_DAY + 2
    assert rumors.rumor_local_max_lines(100) == B.RUMOR_MAX_CAP


def test_rumor_foreign_max_lines_matches_local_scale():
    assert rumors.rumor_foreign_max_lines(0) == 0
    assert rumors.rumor_foreign_max_lines(1) == B.RUMOR_MAX_PER_DAY
    assert rumors.rumor_foreign_max_lines(2) == B.RUMOR_MAX_PER_DAY
    assert rumors.rumor_foreign_max_lines(3) == B.RUMOR_MAX_PER_DAY + 1
    assert rumors.rumor_foreign_max_lines(5) == B.RUMOR_MAX_PER_DAY + 2
    assert rumors.rumor_foreign_max_lines(99) == B.RUMOR_FOREIGN_MAX_CAP
    assert B.RUMOR_FOREIGN_LINE_CHANCE == B.RUMOR_LINE_CHANCE
    assert B.RUMOR_FOREIGN_MAX_CAP == B.RUMOR_MAX_CAP
    for n in range(0, 20):
        assert rumors.rumor_foreign_max_lines(n) == rumors.rumor_local_max_lines(n)


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


def test_compose_foreign_rumor_prefixes_valley():
    snap = _snap(name="Пётр", realm_title="Север")
    text = rumors.compose_foreign_rumor_text(
        snap, rumors.FACT_MIGHT, rumors.TRUTH_FULL, Random(1)
    )
    assert text.startswith("Из долины Север:")
    assert "Пётр" in text


def test_roll_daily_rumors_bounded_and_seeded():
    fiefs = [
        _snap(fief_id=1, name="А"),
        _snap(fief_id=2, name="Б", grain=200, goods=200, might=30),
    ]
    lines = rumors.roll_daily_rumors(fiefs, Random(42))
    assert 0 <= len(lines) <= rumors.rumor_local_max_lines(len(fiefs))
    for line in lines:
        assert line.endswith(".")
        assert "говорят" in line or "шепчут" in line


def test_roll_daily_rumors_more_slots_with_more_players():
    many = [
        _snap(fief_id=i, name=f"Игрок{i}", grain=10 + i, goods=10, might=5 + i)
        for i in range(1, 8)
    ]
    cap = rumors.rumor_local_max_lines(len(many))
    assert cap > B.RUMOR_MAX_PER_DAY
    hit_high = False
    for seed in range(40):
        lines = rumors.roll_daily_rumors(many, Random(seed), line_chance=1.0)
        assert len(lines) <= cap
        if len(lines) > B.RUMOR_MAX_PER_DAY:
            hit_high = True
            break
    assert hit_high


def test_roll_daily_rumors_empty_realm():
    assert rumors.roll_daily_rumors([], Random(1)) == []


def test_roll_valley_day_rumors_foreign_section():
    local = [_snap(fief_id=1, name="А")]
    foreign = [
        _snap(fief_id=10, name="Чужак", realm_title="Юг", might=25),
        _snap(fief_id=11, name="Гость", realm_title="Юг", might=3),
    ]
    found = False
    for seed in range(60):
        bundle = rumors.roll_valley_day_rumors(
            local, foreign, Random(seed)
        )
        assert isinstance(bundle.local, list)
        assert isinstance(bundle.foreign, list)
        assert len(bundle.foreign) <= rumors.rumor_foreign_max_lines(len(foreign))
        if bundle.foreign:
            assert all(line.startswith("Из долины ") for line in bundle.foreign)
            found = True
            break
    assert found


def test_parse_stored_rumors_legacy_list():
    bundle = rumors.parse_stored_rumors(["У А, говорят, дружина тонка."])
    assert bundle.local == ["У А, говорят, дружина тонка."]
    assert bundle.foreign == []


def test_parse_stored_rumors_dict():
    bundle = rumors.parse_stored_rumors(
        {
            "local": ["местная"],
            "foreign": ["Из долины Юг: чужая."],
        }
    )
    assert bundle.local == ["местная"]
    assert bundle.foreign == ["Из долины Юг: чужая."]


def test_format_rumor_section_header_soft():
    section = rumors.format_rumor_section(["У Ивана, говорят, дружина тонка."])
    assert section is not None
    assert "не факты" not in section
    assert "базар может врать" not in section
    assert "Слухи рынка" in section
    assert "• У Ивана" in section


def test_format_rumor_section_with_foreign():
    section = rumors.format_rumor_section(
        ["У Ивана, говорят, дружина тонка."],
        foreign_lines=["Из долины Юг: У Петра, говорят, дружина крепкая."],
    )
    assert section is not None
    assert "Слухи рынка" in section
    assert "Из других долин" in section
    assert "Петра" in section
    assert "\n\n🗺 Из других долин:" in section


def test_format_rumors_pull_empty_explains():
    text = rumors.format_rumors_pull([])
    assert "молчит" in text
    assert "не факты" not in text


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
    assert "👂 Слухи рынка:" in text
    assert "• У Кирилла, говорят, амбар ломится." in text
    assert text.index("Слухи") < text.index("Кирилла")


def test_format_digest_includes_foreign_rumors():
    text = digest.format_digest(
        realm_title="Долина",
        day=5,
        night_lines=[],
        event_line=None,
        market_line=None,
        feud_lines=[],
        sunday_extra=None,
        rumor_lines=["У Кирилла, говорят, амбар ломится."],
        foreign_rumor_lines=["Из долины Юг: У Олега, говорят, дружина тонка."],
    )
    assert "Из других долин" in text
    assert "Олега" in text
    assert "\n\n👂 Слухи рынка:" in text
    assert "\n\n🗺 Из других долин:" in text


def test_compose_event_rumor_accuracy():
    hint = rumors.UpcomingEventHint(kind="minor", key="drought")
    truths = 0
    for seed in range(200):
        line = rumors.compose_event_rumor(hint, Random(seed))
        assert "Засуха" in line or line.startswith("Говорят")
        if "Засуха" in line:
            truths += 1
    assert 0.50 <= truths / 200 <= 0.80


def test_roll_daily_rumors_can_include_event_hint():
    fiefs = [_snap(fief_id=1, name="А")]
    hints = [rumors.UpcomingEventHint(kind="catastrophe", key="bandit_night")]
    found = False
    for seed in range(80):
        lines = rumors.roll_daily_rumors(
            fiefs,
            Random(seed),
            event_hints=hints,
        )
        if any("Ночь бандитов" in ln or "близится беда" in ln for ln in lines):
            found = True
            break
    assert found


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


def test_roll_day_rumors_local_live_foreign_cached():
    """Местные слухи - после экономики; чужие - из снимка старта тика."""
    from unittest.mock import MagicMock

    from app.engine import Engine

    db = MagicMock()
    engine = Engine(db)
    cached_stale_local = [_snap(fief_id=1, name="Старый", might=5)]
    cached_foreign = [_snap(fief_id=9, name="Чужой", realm_title="Юг", might=40)]
    engine._rumor_snapshot_cache = {1: cached_stale_local, 2: cached_foreign}
    engine._upcoming_event_hints = MagicMock(return_value=[])

    live_local = [_snap(fief_id=1, name="Свежий", might=50, grain=200, goods=200)]
    live_calls: list[int] = []

    def live_snap(realm_id, *, realm_title=None):
        live_calls.append(int(realm_id))
        assert int(realm_id) == 1
        return live_local

    engine._rumor_snapshots = live_snap  # type: ignore[method-assign]
    engine.db.list_adjacent_realms = MagicMock(
        side_effect=AssertionError("live adjacent should not be used with cache")
    )

    foreign_pool = engine._foreign_rumor_snapshots(1)
    assert foreign_pool == cached_foreign

    bundle = engine._roll_day_rumors(1)
    assert live_calls == [1]
    assert isinstance(bundle, rumors.DailyRumorBundle)
    # Ролл с line_chance по умолчанию может быть пустым - фиксируем пулы.
    assert engine._foreign_rumor_snapshots(1) == cached_foreign
    assert all(s.name == "Чужой" for s in foreign_pool)
    assert all(s.name != "Старый" for s in live_local)


def test_run_world_tick_installs_and_clears_rumor_cache():
    """Кэш снимков ставится до экономики долин и снимается после цикла."""
    from contextlib import nullcontext
    from unittest.mock import MagicMock, patch

    from app.engine import Engine

    db = MagicMock()
    world = {
        "id": 1,
        "tick_index": 3,
        "day_number": 4,
        "pending_minor_key": "",
        "forced_tick_count": 0,
        "timezone": "Europe/Moscow",
    }
    r1 = {
        "id": 1,
        "world_id": 1,
        "title": "Север",
        "last_economy_tick": 3,
        "chat_id": -100,
    }
    r2 = {
        "id": 2,
        "world_id": 1,
        "title": "Юг",
        "last_economy_tick": 3,
        "chat_id": -101,
    }
    chain = [r1, r2]
    db.get_world.return_value = world
    db.get_or_create_world.return_value = world
    db.list_realms_by_chain.return_value = chain
    db.transaction.return_value = nullcontext()
    db.sync_realms_clock_from_world = MagicMock()
    db.update_world = MagicMock(side_effect=lambda _wid, **fields: world.update(fields))
    db.update_realm = MagicMock(
        side_effect=lambda rid, **fields: next(
            r for r in chain if int(r["id"]) == int(rid)
        ).update(fields)
    )

    engine = Engine(db)
    snap1 = [_snap(fief_id=1, name="A", realm_title="Север")]
    snap2 = [_snap(fief_id=2, name="B", realm_title="Юг")]
    engine._rumor_snapshots = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda rid, **_kw: snap1 if int(rid) == 1 else snap2
    )

    seen_during_economy: list[dict | None] = []

    def realm_tick(rid, tick_slot=None, *, advance_clock=False):
        cache = engine._rumor_snapshot_cache
        assert cache is not None
        assert set(cache) == {1, 2}
        assert cache[1] == snap1
        assert cache[2] == snap2
        # Чужой пул для долины 1 - только снимок Юга, не live adjacent.
        assert engine._foreign_rumor_snapshots(1) == snap2
        seen_during_economy.append(dict(cache))
        return {"realm_id": int(rid), "digest": "d", "chat_id": -1}

    engine.run_realm_tick = MagicMock(side_effect=realm_tick)  # type: ignore[method-assign]

    with patch("app.engine.roll_minor_event", return_value=None):
        engine.run_world_tick(1, tick_slot=0)

    assert len(seen_during_economy) == 2
    assert engine._rumor_snapshot_cache is None
