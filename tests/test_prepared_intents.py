"""Карточка и счётчик исходящих заявок (набеги + обозы)."""
from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("ADMIN_USER_ID", "42")

from app.engine import Engine


def test_prepared_intents_card_empty():
    db = MagicMock()
    db.list_open_raid_intents_for_fief.return_value = []
    db.list_road_caravan_intents_for_fief.return_value = []
    db.list_open_cover_stance_intents_for_fief.return_value = []
    engine = Engine(db)

    text = engine.prepared_intents_card(1)

    assert "Нет подготовленных заявок" in text
    assert engine.prepared_intents_count(1) == 0


def test_prepared_intents_card_lists_raids_caravans_and_cover():
    db = MagicMock()
    db.list_open_raid_intents_for_fief.return_value = [
        {
            "id": 1,
            "status": "open",
            "payload": {"victim_id": 2, "might": 8},
        },
        {
            "id": 2,
            "status": "locked",
            "payload": {"victim_id": 3, "might": 4},
        },
    ]
    db.list_road_caravan_intents_for_fief.return_value = [
        {
            "id": 3,
            "status": "open",
            "payload": {"receiver_id": 4, "res": "grain", "amt": 12},
        },
        {
            "id": 5,
            "status": "locked",
            "payload": {"receiver_id": 4, "res": "goods", "amt": 3},
        },
    ]
    db.list_open_cover_stance_intents_for_fief.return_value = [
        {
            "id": 4,
            "status": "open",
            "payload": {"mode": "any", "budget": 10},
        },
    ]
    fiefs = {
        2: {"id": 2, "name": "Ира"},
        3: {"id": 3, "name": "Оля"},
        4: {"id": 4, "name": "Кирилл"},
    }
    db.get_fief.side_effect = lambda fid: fiefs.get(int(fid))
    engine = Engine(db)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])  # type: ignore[method-assign]

    text = engine.prepared_intents_card(1)

    assert "на Ира: 8 силы (открыта)" in text
    assert "на Оля: 4 силы (закрыта)" in text
    assert "к Кирилл: 12" in text
    assert "можно вернуть" in text
    assert "закрыт" in text
    assert "Любого союзника: 10 силы (можно снять)" in text
    assert engine.prepared_intents_count(1) == 5


def test_prepared_intent_status_lines_include_caravan():
    db = MagicMock()
    db.list_open_raid_intents_for_fief.return_value = [
        {
            "id": 1,
            "status": "open",
            "payload": {"victim_id": 2, "might": 5},
        }
    ]
    db.list_road_caravan_intents_for_fief.return_value = [
        {
            "id": 2,
            "status": "open",
            "payload": {"receiver_id": 3, "res": "goods", "amt": 15},
        }
    ]
    db.list_open_cover_stance_intents_for_fief.return_value = [
        {
            "id": 3,
            "status": "locked",
            "payload": {"mode": "any", "budget": 8},
        }
    ]
    fiefs = {
        2: {"id": 2, "name": "Ира"},
        3: {"id": 3, "name": "Бета"},
    }
    db.get_fief.side_effect = lambda fid: fiefs.get(int(fid))
    engine = Engine(db)
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])  # type: ignore[method-assign]

    lines = engine._prepared_intent_status_lines(1)

    assert lines[0] == "Заявки:"
    assert any("набег на Ира: 5 силы (открыта)" in line for line in lines)
    assert any("обоз к Бета: 15" in line for line in lines)
    assert any("застава (Любого союзника): 8 силы (закрыта)" in line for line in lines)


def test_list_prepared_intents_normalizes_empty_mocks():
    """MagicMock DB без return_value не должен давать пустой заголовок Заявки:."""
    db = MagicMock()
    engine = Engine(db)

    raids, caravans, covers = engine.list_prepared_intents(1)

    assert raids == []
    assert caravans == []
    assert covers == []
    assert engine.prepared_intents_count(1) == 0
    assert engine._prepared_intent_status_lines(1) == []


def test_status_card_omits_prepared_block_when_empty():
    from datetime import date, datetime, timezone
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    from app.domain.production import Production

    db = MagicMock()
    db.list_open_raid_intents_for_fief.return_value = []
    db.list_road_caravan_intents_for_fief.return_value = []
    db.list_open_cover_stance_intents_for_fief.return_value = []
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=Production(grain=1.0, goods=1.0, might=0.0, defense=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    db.get_fief.return_value = {
        "id": 1,
        "name": "Альфа",
        "realm_id": 3,
        "grain": 10,
        "goods": 10,
        "might": 5,
        "actions": 1,
        "hungry": False,
        "onboard_step": 4,
        "patrol_until_tick": None,
        "shield_until_tick": None,
        "last_active_tick": 1,
    }
    db.get_realm.return_value = {
        "id": 3,
        "day_number": 5,
        "tick_index": 1,
        "timezone": "Europe/Moscow",
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 1,
    }
    db.fief_tiles.return_value = [{"is_overgrown": False}]
    fixed_now = datetime(2026, 7, 16, 14, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    with patch("app.engine.datetime", _FrozenDateTime):
        text = engine.status_card(1)

    assert "Заявки:" not in text


def test_status_card_includes_prepared_raid_and_caravan():
    from datetime import date, datetime
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    from app.domain.production import Production

    db = MagicMock()
    db.list_open_raid_intents_for_fief.return_value = [
        {
            "id": 1,
            "status": "open",
            "payload": {"victim_id": 2, "might": 6},
        }
    ]
    db.list_road_caravan_intents_for_fief.return_value = [
        {
            "id": 2,
            "status": "open",
            "payload": {"receiver_id": 3, "res": "grain", "amt": 9},
        }
    ]
    db.list_open_cover_stance_intents_for_fief.return_value = []
    fiefs = {
        1: {
            "id": 1,
            "name": "Альфа",
            "realm_id": 3,
            "grain": 10,
            "goods": 10,
            "might": 5,
            "actions": 1,
            "hungry": False,
            "onboard_step": 4,
            "patrol_until_tick": None,
            "shield_until_tick": None,
            "last_active_tick": 1,
        },
        2: {"id": 2, "name": "Ира"},
        3: {"id": 3, "name": "Бета"},
    }
    db.get_fief.side_effect = lambda fid: fiefs.get(int(fid))
    db.get_realm.return_value = {
        "id": 3,
        "day_number": 5,
        "tick_index": 1,
        "timezone": "Europe/Moscow",
        "last_tick_local_date": date(2026, 7, 16),
        "last_tick_slot": 1,
    }
    db.fief_tiles.return_value = [{"is_overgrown": False}]
    engine = Engine(db)
    engine.collect_for_fief = MagicMock(return_value=[])  # type: ignore[method-assign]
    engine.fief_prod = MagicMock(  # type: ignore[method-assign]
        return_value=Production(grain=1.0, goods=1.0, might=0.0, defense=0.0)
    )
    engine.barn_level = MagicMock(return_value=0)  # type: ignore[method-assign]
    engine.fief_label = MagicMock(side_effect=lambda f: f["name"])  # type: ignore[method-assign]
    fixed_now = datetime(2026, 7, 16, 14, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now
            return fixed_now.astimezone(tz)

    with patch("app.engine.datetime", _FrozenDateTime):
        text = engine.status_card(1)

    assert "Заявки:" in text
    assert "набег на Ира: 6 силы (открыта)" in text
    assert "обоз к Бета: 9" in text
