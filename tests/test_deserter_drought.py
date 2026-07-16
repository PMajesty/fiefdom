"""Засуха без личного выкупа."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.domain import events
from app.domain.events import catastrophe_effect, minor_effect
from app.engine import Engine


def test_shipped_includes_drought_not_deserter():
    assert "deserter" not in events.SHIPPED_MINOR_KEYS
    assert "deserter" not in events.MINOR_EVENTS
    assert "drought" in events.SHIPPED_MINOR_KEYS
    assert "trader" not in events.SHIPPED_MINOR_KEYS


def test_drought_has_no_personal_mitigate():
    eff = minor_effect("drought")
    assert "mitigate" not in eff
    assert float(eff["farm_mult"]) < 1.0
    meta = events.MINOR_EVENTS["drought"]
    assert meta.get("button_labels") is None
    assert "полив" not in meta["digest_line"].lower()


def test_cattle_plague_has_no_personal_mitigate():
    eff = events.catastrophe_effect("cattle_plague")
    assert "mitigate" not in eff
    meta = events.CATASTROPHES["cattle_plague"]
    assert meta.get("button_labels") is None


def test_realm_farm_mult_stacks_cattle_plague_with_minors():
    db = MagicMock()
    db.get_active_events.return_value = [{"id": 1, "event_key": "cattle_plague"}]
    engine = Engine(db)
    plague = float(catastrophe_effect("cattle_plague")["farm_mult"])
    drought = float(minor_effect("drought")["farm_mult"])
    harvest = float(minor_effect("harvest")["farm_mult"])

    assert engine._realm_farm_mult({"id": 1, "active_minor_key": None}) == plague
    assert engine._realm_farm_mult({"id": 1, "active_minor_key": "drought"}) == drought * plague
    assert engine._realm_farm_mult({"id": 1, "active_minor_key": "harvest"}) == harvest * plague
    assert "половин" not in events.CATASTROPHES["cattle_plague"]["canned_narrative"]
