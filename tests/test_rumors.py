"""Тесты капельных слухов: микс, cadence, тишина, архив, digest без слухов."""
from __future__ import annotations

from datetime import datetime, timezone
from random import Random
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app import balance as B
from app.domain import digest, rumors
from app.engine import Engine


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


def test_compose_full_wealth_uses_true_band():
    snap = _snap(grain=10, goods=5)
    text = rumors.compose_rumor_text(snap, rumors.FACT_WEALTH, rumors.TRUTH_FULL, Random(1))
    assert "Иван" in text
    assert "тощая" in text


def test_compose_fuzzy_wealth_single_hedge():
    snap = _snap(grain=10, goods=5)
    text = rumors.compose_rumor_text(
        snap, rumors.FACT_WEALTH, rumors.TRUTH_FUZZY, Random(1)
    )
    assert text.startswith("Про Иван:")
    assert "говорят" not in text
    assert "шепчут" not in text
    assert "будто" in text or "вроде" in text


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


def test_fluff_templates_pool_size():
    assert len(rumors._FLUFF_TEMPLATES) == 55
    assert all("{name}" in line for line in rumors._FLUFF_TEMPLATES)
    assert all(
        bad not in " ".join(rumors._FLUFF_TEMPLATES)
        for bad in ("зарплат", "рейд", "табуретка", "мозолями на языке")
    )


def test_compose_fluff_named_and_opener():
    text = rumors.compose_fluff_rumor("Кирилл", Random(3))
    assert "Кирилл" in text
    assert any(text.startswith(op) for op in rumors.RUMOR_OPENERS)


def test_roll_rumor_line_fluff_rate_near_quarter():
    local = [_snap(fief_id=1, name="А")]
    foreign = [_snap(fief_id=2, name="Б", realm_title="Юг")]
    fluff = 0
    n = 800
    for seed in range(n):
        line = rumors.roll_rumor_line(local, foreign, (), Random(seed))
        assert line
        if any(line.startswith(op) for op in rumors.RUMOR_OPENERS):
            fluff += 1
    rate = fluff / n
    assert 0.18 <= rate <= 0.32


def test_roll_rumor_line_mixed_pool_can_foreign():
    local = [_snap(fief_id=1, name="Местный")]
    foreign = [
        _snap(fief_id=9, name="Чужак", realm_title="Юг", might=40, grain=200, goods=200)
    ]
    found = False
    for seed in range(120):
        line = rumors.roll_rumor_line(local, foreign, (), Random(seed))
        if line and line.startswith("Из долины Юг:"):
            found = True
            break
    assert found


def test_roll_rumor_line_can_event_hint():
    local = [_snap(fief_id=1, name="А")]
    hints = [rumors.UpcomingEventHint(kind="catastrophe", key="bandit_night")]
    found = False
    for seed in range(100):
        line = rumors.roll_rumor_line(local, (), hints, Random(seed))
        if line and ("Ночь бандитов" in line or "близится беда" in line):
            found = True
            break
    assert found


def test_rumor_count_for_window_weights():
    counts = [rumors.rumor_count_for_window(Random(s)) for s in range(1000)]
    assert all(c in (1, 2, 3) for c in counts)
    mean = sum(counts) / len(counts)
    assert 1.7 <= mean <= 2.2
    twos = sum(1 for c in counts if c == 2)
    assert twos > sum(1 for c in counts if c == 1)
    assert twos > sum(1 for c in counts if c == 3)


def test_rumor_lines_per_wave_always_bundle():
    sizes = [rumors.rumor_lines_per_wave(Random(s)) for s in range(400)]
    assert all(s in (2, 3) for s in sizes)
    assert 0.50 <= sizes.count(2) / len(sizes) <= 0.70


def test_format_rumor_wave_chains_with_bridges():
    text = rumors.format_rumor_wave(
        ["Первая сплетня.", "Вторая сплетня.", "Третья сплетня."]
    )
    assert text.startswith("Первая сплетня.")
    assert "\n" in text
    assert any(bridge in text for bridge in rumors.RUMOR_CHAIN_BRIDGES)
    assert "Вторая сплетня." in text
    assert "Третья сплетня." in text


def test_roll_rumor_wave_returns_multiple_distinct():
    local = [
        _snap(fief_id=1, name="А"),
        _snap(fief_id=2, name="Б", grain=200, goods=200, might=40),
    ]
    foreign = [_snap(fief_id=9, name="В", realm_title="Юг", might=5)]
    wave = rumors.roll_rumor_wave(local, foreign, (), Random(7), line_count=3)
    assert 2 <= len(wave) <= 3
    assert len(set(wave)) == len(wave)
    assert all(wave)


def test_in_rumor_quiet_hours():
    tz = ZoneInfo("Europe/Moscow")
    assert rumors.in_rumor_quiet_hours(datetime(2026, 7, 17, 21, 0, tzinfo=tz))
    assert rumors.in_rumor_quiet_hours(datetime(2026, 7, 17, 23, 30, tzinfo=tz))
    assert rumors.in_rumor_quiet_hours(datetime(2026, 7, 18, 0, 0, tzinfo=tz))
    assert rumors.in_rumor_quiet_hours(datetime(2026, 7, 18, 7, 59, tzinfo=tz))
    assert not rumors.in_rumor_quiet_hours(datetime(2026, 7, 18, 8, 0, tzinfo=tz))
    assert not rumors.in_rumor_quiet_hours(datetime(2026, 7, 17, 12, 0, tzinfo=tz))
    assert not rumors.in_rumor_quiet_hours(datetime(2026, 7, 17, 20, 59, tzinfo=tz))


def test_plan_due_times_daytime_window_inside_bounds():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 10, 0, tzinfo=tz)
    end = datetime(2026, 7, 17, 13, 0, tzinfo=tz)
    dues = rumors.plan_rumor_due_times(start, end, 3, rng=Random(11))
    assert 1 <= len(dues) <= 3
    assert dues == sorted(dues)
    assert len(dues) == len(set(dues))
    for due in dues:
        assert start <= due < end
        assert not rumors.in_rumor_quiet_hours(due)


def test_plan_due_times_overnight_only_morning_slice():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 19, 0, tzinfo=tz)
    end = datetime(2026, 7, 18, 10, 0, tzinfo=tz)
    for seed in range(40):
        dues = rumors.plan_rumor_due_times(start, end, 2, rng=Random(seed))
        assert dues
        for due in dues:
            assert due.date() == end.date()
            assert due.hour >= 8
            assert due < end
            assert not rumors.in_rumor_quiet_hours(due)
            assert due >= datetime(2026, 7, 18, 8, 0, tzinfo=tz)


def test_plan_due_times_never_in_quiet():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 16, 0, tzinfo=tz)
    end = datetime(2026, 7, 18, 10, 0, tzinfo=tz)
    for seed in range(30):
        for due in rumors.plan_rumor_due_times(start, end, 2, rng=Random(seed)):
            assert not rumors.in_rumor_quiet_hours(due)


def test_parse_stored_rumors_flat_and_legacy():
    assert rumors.parse_stored_rumors(["а", "б"]) == ["а", "б"]
    assert rumors.parse_stored_rumors(
        {"local": ["местная"], "foreign": ["Из долины Юг: чужая."]}
    ) == ["местная", "Из долины Юг: чужая."]
    assert rumors.parse_stored_rumors(None) == []
    assert rumors.parse_stored_rumors({}) == []


def test_format_rumors_pull_archive_note():
    text = rumors.format_rumors_pull(["У Ивана, говорят, дружина тонка."])
    assert "Недавний шёпот" in text
    assert "У Ивана" in text
    assert "днём в группе" in text
    assert "Слухи рынка:" not in text
    assert "Из других долин" not in text


def test_format_rumors_pull_empty_explains():
    text = rumors.format_rumors_pull([])
    assert "молчит" in text
    assert "групповом чате" in text


def test_format_digest_omits_rumors():
    text = digest.format_digest(
        realm_title="Долина",
        day=5,
        night_lines=[],
        event_line=None,
        feud_lines=[],
        sunday_extra="🏅 Титулы: тест.",
    )
    assert "Слухи" not in text
    assert "👂" not in text
    assert "🏅 Титулы: тест." in text


def test_compose_event_rumor_accuracy():
    hint = rumors.UpcomingEventHint(kind="minor", key="drought")
    truths = 0
    for seed in range(200):
        line = rumors.compose_event_rumor(hint, Random(seed))
        assert "Засуха" in line or line.startswith("Говорят")
        if "Засуха" in line:
            truths += 1
    assert 0.50 <= truths / 200 <= 0.80


def test_truth_weights_cover_falsehood():
    assert 0 < B.RUMOR_TRUTH_FULL < 1
    assert 0 < B.RUMOR_TRUTH_FUZZY < 1
    assert B.RUMOR_TRUTH_FULL + B.RUMOR_TRUTH_FUZZY < 1
    false_rate = 1.0 - B.RUMOR_TRUTH_FULL - B.RUMOR_TRUTH_FUZZY
    assert 0.05 <= false_rate <= 0.30


def test_append_rumor_archive_trims():
    lines = [f"l{i}" for i in range(20)]
    out = rumors.append_rumor_archive(lines[:-1], "l19", max_lines=12)
    assert len(out) == 12
    assert out[0] == "l8"
    assert out[-1] == "l19"


def test_append_rumor_archive_default_cap():
    assert B.RUMOR_ARCHIVE_MAX == 18
    archive: list[str] = []
    for i in range(B.RUMOR_ARCHIVE_MAX + 3):
        archive = rumors.append_rumor_archive(archive, f"l{i}")
    assert len(archive) == B.RUMOR_ARCHIVE_MAX
    assert archive[0] == "l3"
    assert archive[-1] == f"l{B.RUMOR_ARCHIVE_MAX + 2}"


def test_maybe_due_rumors_and_ack_idempotent():
    tz = ZoneInfo("Europe/Moscow")
    local_now = datetime(2026, 7, 17, 12, 0, tzinfo=tz)
    due = datetime(2026, 7, 17, 11, 30, tzinfo=tz)
    realm = {
        "id": 1,
        "rumor_queue": [due.isoformat()],
        "last_rumor_lines": [],
        "title": "Север",
        "tick_index": 3,
        "pending_minor_key": None,
        "next_catastrophe_tick": None,
        "next_catastrophe_key": None,
    }
    db = MagicMock()
    db.list_realms_by_chain.return_value = [realm]
    db.get_realm.return_value = realm
    db.list_adjacent_realms.return_value = []
    db.list_fiefs.return_value = [
        {
            "id": 7,
            "frozen": False,
            "grain": 40,
            "goods": 40,
            "might": 12,
            "patrol_until_tick": None,
            "name": "Двор",
        },
        {
            "id": 8,
            "frozen": False,
            "grain": 200,
            "goods": 80,
            "might": 28,
            "patrol_until_tick": 99,
            "name": "Хутор",
        },
    ]
    db.fief_tiles.return_value = []
    engine = Engine(db)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])  # type: ignore[method-assign]

    due_items = engine.maybe_due_rumors(1, local_now)
    assert len(due_items) == 1
    assert due_items[0]["realm_id"] == 1
    assert due_items[0]["text"]
    lines = due_items[0]["lines"]
    assert isinstance(lines, list) and len(lines) >= 2
    assert all(line in due_items[0]["text"] for line in lines)
    # Пока не ack - due остаётся.
    assert len(engine.maybe_due_rumors(1, local_now)) == 1

    engine.acknowledge_rumor_posted(
        1, due_items[0]["due"], due_items[0]["text"], lines=lines
    )
    stored = db.update_realm.call_args.kwargs
    assert stored["rumor_queue"] == []
    for line in lines:
        assert line in stored["last_rumor_lines"]
    realm["rumor_queue"] = stored["rumor_queue"]
    realm["last_rumor_lines"] = stored["last_rumor_lines"]
    assert engine.maybe_due_rumors(1, local_now) == []


def test_maybe_due_rumors_skips_quiet_hours():
    tz = ZoneInfo("Europe/Moscow")
    local_now = datetime(2026, 7, 17, 22, 0, tzinfo=tz)
    due = datetime(2026, 7, 17, 12, 0, tzinfo=tz)
    db = MagicMock()
    db.list_realms_by_chain.return_value = [
        {"id": 1, "rumor_queue": [due.isoformat()], "last_rumor_lines": []}
    ]
    engine = Engine(db)
    assert engine.maybe_due_rumors(1, local_now) == []


def test_failed_send_keeps_queue_until_ack():
    """Симуляция: due виден снова, пока acknowledge не вызван."""
    tz = ZoneInfo("Europe/Moscow")
    local_now = datetime(2026, 7, 17, 12, 0, tzinfo=tz)
    due = datetime(2026, 7, 17, 11, 0, tzinfo=tz)
    realm = {
        "id": 2,
        "rumor_queue": [due.isoformat()],
        "last_rumor_lines": ["старая"],
        "title": "Юг",
        "tick_index": 1,
        "pending_minor_key": None,
        "next_catastrophe_tick": None,
        "next_catastrophe_key": None,
    }
    db = MagicMock()
    db.list_realms_by_chain.return_value = [realm]
    db.get_realm.return_value = realm
    db.list_adjacent_realms.return_value = []
    db.list_fiefs.return_value = []
    engine = Engine(db)
    first = engine.maybe_due_rumors(2, local_now)
    second = engine.maybe_due_rumors(2, local_now)
    assert len(first) == 1 and len(second) == 1
    assert first[0]["due"] == second[0]["due"]
    assert first[0]["text"] is None


def test_plan_world_rumor_queues_clears_when_no_window():
    db = MagicMock()
    opened = datetime(2026, 7, 17, 19, 5, tzinfo=timezone.utc)
    world = {"id": 1, "tick_phase": "play", "play_opened_at": opened}
    realms = [{"id": 3, "rumor_queue": ["2026-07-17T12:00:00+03:00"]}]
    db.get_world.return_value = world
    db.list_realms_by_chain.return_value = realms
    engine = Engine(db)
    engine.play_window_bounds_for_world = MagicMock(return_value=None)  # type: ignore[method-assign]
    engine.plan_world_rumor_queues(1)
    db.update_realm.assert_called_once_with(3, rumor_queue=[])
    db.update_world.assert_called_once_with(
        1, rumor_plan_play_opened_at=opened
    )


def test_ensure_rumor_queues_planned_fills_unmarked_play():
    db = MagicMock()
    opened = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    world = {
        "id": 1,
        "tick_phase": "play",
        "play_opened_at": opened,
        "rumor_plan_play_opened_at": None,
    }
    db.get_world.return_value = world
    engine = Engine(db)
    engine.world_tick_incomplete = MagicMock(return_value=False)  # type: ignore[method-assign]
    engine.plan_world_rumor_queues = MagicMock()  # type: ignore[method-assign]
    engine.ensure_rumor_queues_planned(1)
    engine.plan_world_rumor_queues.assert_called_once_with(1)


def test_ensure_rumor_queues_planned_skips_drained_same_window():
    """После публикации dues очередь пуста, но план на это окно уже был."""
    db = MagicMock()
    opened = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    world = {
        "id": 1,
        "tick_phase": "play",
        "play_opened_at": opened,
        "rumor_plan_play_opened_at": opened,
    }
    db.get_world.return_value = world
    engine = Engine(db)
    engine.world_tick_incomplete = MagicMock(return_value=False)  # type: ignore[method-assign]
    engine.plan_world_rumor_queues = MagicMock()  # type: ignore[method-assign]
    engine.ensure_rumor_queues_planned(1)
    engine.plan_world_rumor_queues.assert_not_called()
