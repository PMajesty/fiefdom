"""Тесты капельных слухов: правда, cadence 0/1, тишина, архив, digest без слухов."""
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


def test_compose_wealth_uses_true_band():
    snap = _snap(grain=10, goods=5)
    text = rumors.compose_rumor_text(snap, rumors.FACT_WEALTH, Random(1))
    assert "Иван" in text
    assert "тощая" in text
    assert text.startswith("У Иван, говорят,")


def test_compose_might_uses_true_band():
    snap = _snap(might=25)
    text = rumors.compose_rumor_text(snap, rumors.FACT_MIGHT, Random(1))
    assert "копий во дворе много" in text


def test_compose_patrol_matches_state():
    on = rumors.compose_rumor_text(
        _snap(patrol_active=True), rumors.FACT_PATROL, Random(0)
    )
    off = rumors.compose_rumor_text(
        _snap(patrol_active=False), rumors.FACT_PATROL, Random(0)
    )
    assert "дозор ходит" in on
    assert "без дозора" in off


def test_compose_building_names_russian():
    snap = _snap(buildings=((B.BLD_WATCH, 2),))
    text = rumors.compose_rumor_text(snap, rumors.FACT_BUILDING, Random(2))
    assert "сторожка" in text.lower()
    assert "II" in text


def test_rumor_subject_name_strips_telegram_at():
    assert rumors.rumor_subject_name("Усадьба @artem_x") == "Усадьба artem_x"
    assert rumors.rumor_subject_name("Усадьба Иван") == "Усадьба Иван"
    assert rumors.rumor_subject_name("") == ""


def test_compose_rumor_text_keeps_username_without_at():
    name = rumors.rumor_subject_name("Усадьба @artem_x")
    snap = _snap(name=name, grain=10, goods=0)
    text = rumors.compose_rumor_text(snap, rumors.FACT_WEALTH, Random(1))
    assert "Усадьба artem_x" in text
    assert "@" not in text


def test_compose_foreign_rumor_prefixes_valley():
    snap = _snap(name="Пётр", realm_title="Север")
    text = rumors.compose_foreign_rumor_text(
        snap, rumors.FACT_MIGHT, Random(1)
    )
    assert text.startswith("Из долины Север:")
    assert "Пётр" in text


def test_roll_rumor_line_empty_pool_without_hints():
    assert rumors.roll_rumor_line((), (), (), Random(1)) is None


def test_roll_rumor_line_is_true_gameplay_intel():
    local = [_snap(fief_id=1, name="А", grain=10, goods=5, might=5)]
    for seed in range(80):
        line = rumors.roll_rumor_line(local, (), (), Random(seed))
        assert line
        assert line.startswith("У А, говорят,")
        assert "будто" not in line
        assert "вроде" not in line


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


def test_rumor_count_for_window_zero_or_one():
    counts = [rumors.rumor_count_for_window(Random(s)) for s in range(1000)]
    assert all(c in (0, 1) for c in counts)
    mean = sum(counts) / len(counts)
    assert 0.30 <= mean <= 0.50
    zeros = sum(1 for c in counts if c == 0)
    ones = sum(1 for c in counts if c == 1)
    assert zeros > ones


def test_format_rumor_wave_single_line():
    assert rumors.format_rumor_wave(["Первая сплетня.", "Вторая."]) == "Первая сплетня."
    assert rumors.format_rumor_wave([]) == ""
    assert rumors.format_rumor_wave(["  "]) == ""


def test_roll_rumor_wave_at_most_one_line():
    local = [
        _snap(fief_id=1, name="А"),
        _snap(fief_id=2, name="Б", grain=200, goods=200, might=40),
    ]
    foreign = [_snap(fief_id=9, name="В", realm_title="Юг", might=5)]
    for seed in range(40):
        wave = rumors.roll_rumor_wave(local, foreign, (), Random(seed))
        assert len(wave) <= 1
        if wave:
            assert wave[0]


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
    dues = rumors.plan_rumor_due_times(start, end, 1, rng=Random(11))
    assert len(dues) == 1
    assert start <= dues[0] < end
    assert not rumors.in_rumor_quiet_hours(dues[0])


def test_plan_due_times_count_zero_empty():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 10, 0, tzinfo=tz)
    end = datetime(2026, 7, 17, 13, 0, tzinfo=tz)
    assert rumors.plan_rumor_due_times(start, end, 0, rng=Random(1)) == []


def test_plan_due_times_clamps_above_one():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 10, 0, tzinfo=tz)
    end = datetime(2026, 7, 17, 13, 0, tzinfo=tz)
    dues = rumors.plan_rumor_due_times(start, end, 5, rng=Random(3))
    assert len(dues) == 1


def test_plan_due_times_overnight_only_morning_slice():
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 7, 17, 19, 0, tzinfo=tz)
    end = datetime(2026, 7, 18, 10, 0, tzinfo=tz)
    for seed in range(40):
        dues = rumors.plan_rumor_due_times(start, end, 1, rng=Random(seed))
        assert len(dues) == 1
        due = dues[0]
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
        for due in rumors.plan_rumor_due_times(start, end, 1, rng=Random(seed)):
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
    assert "днём в личку" in text
    assert "не врёт" in text
    assert "Слухи рынка:" not in text
    assert "Из других долин" not in text


def test_format_rumors_pull_empty_explains():
    text = rumors.format_rumors_pull([])
    assert "молчит" in text
    assert "в личку" in text


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


def test_compose_event_rumor_always_true():
    hint = rumors.UpcomingEventHint(kind="minor", key="drought")
    for _ in range(20):
        line = rumors.compose_event_rumor(hint)
        assert "Засуха" in line
    bad = rumors.UpcomingEventHint(kind="catastrophe", key="bandit_night")
    assert "Ночь бандитов" in rumors.compose_event_rumor(bad)


def test_append_rumor_archive_trims():
    lines = [f"l{i}" for i in range(20)]
    out = rumors.append_rumor_archive(lines[:-1], "l19", max_lines=12)
    assert len(out) == 12
    assert out[0] == "l8"
    assert out[-1] == "l19"


def test_append_rumor_archive_default_cap():
    assert B.RUMOR_ARCHIVE_MAX == 12
    archive: list[str] = []
    for i in range(B.RUMOR_ARCHIVE_MAX + 3):
        archive = rumors.append_rumor_archive(archive, f"l{i}")
    assert len(archive) == B.RUMOR_ARCHIVE_MAX
    assert archive[0] == "l3"
    assert archive[-1] == f"l{B.RUMOR_ARCHIVE_MAX + 2}"


def test_rumor_snapshots_strip_at_from_fief_label():
    db = MagicMock()
    db.get_realm.return_value = {"id": 1, "tick_index": 0, "title": "Север"}
    db.list_fiefs.return_value = [
        {
            "id": 7,
            "frozen": False,
            "grain": 1,
            "goods": 1,
            "might": 1,
            "patrol_until_tick": None,
            "name": "Усадьба @artem_x",
        }
    ]
    db.fief_tiles.return_value = []
    engine = Engine(db)
    engine.fief_label = MagicMock(return_value="Усадьба @artem_x")  # type: ignore[method-assign]
    snaps = engine._rumor_snapshots(1)
    assert len(snaps) == 1
    assert snaps[0].name == "Усадьба artem_x"
    assert "@" not in snaps[0].name


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
    assert isinstance(lines, list) and len(lines) == 1
    assert lines[0] == due_items[0]["text"]
    # Пока не ack - due остаётся.
    assert len(engine.maybe_due_rumors(1, local_now)) == 1

    engine.acknowledge_rumor_posted(
        1, due_items[0]["due"], due_items[0]["text"], lines=lines
    )
    stored = db.update_realm.call_args.kwargs
    assert stored["rumor_queue"] == []
    assert lines[0] in stored["last_rumor_lines"]
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
    db.list_realms_by_chain.return_value = [{"id": 3, "rumor_queue": []}]
    engine = Engine(db)
    engine.world_tick_incomplete = MagicMock(return_value=False)  # type: ignore[method-assign]
    engine.plan_world_rumor_queues = MagicMock()  # type: ignore[method-assign]
    engine.ensure_rumor_queues_planned(1)
    engine.plan_world_rumor_queues.assert_not_called()


def test_clamp_rumor_dues_keeps_earliest():
    tz = ZoneInfo("Europe/Moscow")
    a = datetime(2026, 7, 17, 11, 0, tzinfo=tz)
    b = datetime(2026, 7, 17, 12, 0, tzinfo=tz)
    c = datetime(2026, 7, 17, 13, 0, tzinfo=tz)
    assert rumors.clamp_rumor_dues([c, a, b], max_count=1) == [a]
    assert rumors.clamp_rumor_dues([c, a, b], max_count=0) == []
    assert rumors.clamp_rumor_dues([], max_count=1) == []


def test_ensure_clamps_stale_multi_due_queue():
    """Mid-play после смены cadence: старая очередь из 3 due срезается до 1."""
    db = MagicMock()
    opened = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)
    tz = ZoneInfo("Europe/Moscow")
    dues = [
        datetime(2026, 7, 17, 11, 0, tzinfo=tz).isoformat(),
        datetime(2026, 7, 17, 12, 0, tzinfo=tz).isoformat(),
        datetime(2026, 7, 17, 13, 0, tzinfo=tz).isoformat(),
    ]
    realm = {"id": 3, "rumor_queue": list(dues)}
    world = {
        "id": 1,
        "tick_phase": "play",
        "play_opened_at": opened,
        "rumor_plan_play_opened_at": opened,
    }
    db.get_world.return_value = world
    db.list_realms_by_chain.return_value = [realm]
    engine = Engine(db)
    engine.world_tick_incomplete = MagicMock(return_value=False)  # type: ignore[method-assign]
    engine.plan_world_rumor_queues = MagicMock()  # type: ignore[method-assign]
    engine.ensure_rumor_queues_planned(1)
    engine.plan_world_rumor_queues.assert_not_called()
    db.update_realm.assert_called_once_with(
        3, rumor_queue=[dues[0]]
    )


def test_maybe_due_emits_at_most_one_from_stale_queue():
    tz = ZoneInfo("Europe/Moscow")
    local_now = datetime(2026, 7, 17, 14, 0, tzinfo=tz)
    dues = [
        datetime(2026, 7, 17, 11, 0, tzinfo=tz).isoformat(),
        datetime(2026, 7, 17, 12, 0, tzinfo=tz).isoformat(),
        datetime(2026, 7, 17, 13, 0, tzinfo=tz).isoformat(),
    ]
    realm = {
        "id": 1,
        "rumor_queue": list(dues),
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
        }
    ]
    db.fief_tiles.return_value = []
    engine = Engine(db)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])  # type: ignore[method-assign]
    items = engine.maybe_due_rumors(1, local_now)
    assert len(items) == 1
    assert items[0]["due"] == dues[0]
    db.update_realm.assert_called_with(1, rumor_queue=[dues[0]])


def test_patch_note_rumor_true_sparse_registered():
    from app.domain.patch_notes import PATCH_NOTES

    ids = {n.id for n in PATCH_NOTES}
    assert "rumor_true_sparse_v1" in ids


def test_patch_note_public_notices_to_dm_registered():
    from app.domain.patch_notes import PATCH_NOTES

    note = next(n for n in PATCH_NOTES if n.id == "public_notices_to_dm_v1")
    body = " ".join(note.body_lines)
    assert "личку" in body
    assert "групповой чат" in body
    assert "мелкий обоз" in body
