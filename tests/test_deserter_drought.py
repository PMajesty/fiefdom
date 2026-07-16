"""Засуха без личного выкупа."""
from __future__ import annotations

from app.domain import events
from app.domain.events import minor_effect


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
