"""Контракты эффектов: полнота wiring и отсутствие мертвых полей у shipped-ключей."""
from __future__ import annotations

import pytest

from app.domain.event_apply import (
    CatastropheResolveCtx,
    INSTANT_MINOR_HANDLER_KEYS,
    RESOLVE_CATASTROPHE_HANDLER_KEYS,
    resolve_catastrophe,
)
from app.domain.event_contracts import (
    EFFECT_CONTRACTS,
    ongoing_field_to_kind,
    shipped_contract_keys,
    validate_effect_contracts,
)
from app.domain.events import SHIPPED_CATASTROPHE_KEYS, SHIPPED_MINOR_KEYS
from app.domain.modifiers import (
    LIVE_READ_MODIFIER_KINDS,
    MODIFIER_SET_KIND_READERS,
    EffectKind,
)
from app.engine import ENGINE_CONSUMED_MODIFIER_KINDS, Engine
from tests.live_path_scan import live_path_source


def test_effect_contracts_valid():
    errors = validate_effect_contracts()
    assert errors == [], "\n".join(errors)


def test_ongoing_wiring_is_single_source_from_contracts():
    mapping = ongoing_field_to_kind()
    assert mapping["farm_mult"] is EffectKind.FARM_MULT
    assert mapping["raids_ignore_patrol"] is EffectKind.FOG_IGNORES_PATROL
    assert mapping["trade_bonus_frac"] is EffectKind.TRADE_BONUS_FRAC
    assert mapping["upgrade_cost_mult"] is EffectKind.UPGRADE_COST_MULT
    assert mapping["trade_gift_grain"] is EffectKind.TRADE_GIFT_GRAIN


def test_declared_ongoing_kinds_are_reachable_on_engine_paths():
    assert ENGINE_CONSUMED_MODIFIER_KINDS == LIVE_READ_MODIFIER_KINDS
    from app.domain.tile_entities import ENTITY_KIND_CONTRACTS

    declared = {
        decl.kind
        for contract in EFFECT_CONTRACTS.values()
        for decl in contract.ongoing
    }
    declared |= {
        decl.kind
        for contract in ENTITY_KIND_CONTRACTS.values()
        for decl in contract.modifiers
    }
    assert declared <= LIVE_READ_MODIFIER_KINDS
    src = live_path_source()
    for kind in declared:
        method = MODIFIER_SET_KIND_READERS[kind]
        assert f".{method}()" in src


def test_every_shipped_key_has_contract():
    assert frozenset(EFFECT_CONTRACTS) == shipped_contract_keys()
    assert SHIPPED_MINOR_KEYS <= frozenset(EFFECT_CONTRACTS)
    assert SHIPPED_CATASTROPHE_KEYS <= frozenset(EFFECT_CONTRACTS)


def test_shipped_instant_and_resolve_handlers_match_contracts():
    for key in SHIPPED_MINOR_KEYS:
        contract = EFFECT_CONTRACTS[key]
        assert contract.has_instant_handler == (key in INSTANT_MINOR_HANDLER_KEYS)
    for key in SHIPPED_CATASTROPHE_KEYS:
        contract = EFFECT_CONTRACTS[key]
        assert contract.has_resolve_handler is True
        assert key in RESOLVE_CATASTROPHE_HANDLER_KEYS


def test_bandit_night_dead_fields_removed_from_shipped_table():
    from app.domain.events import catastrophe_effect

    bandit = catastrophe_effect("bandit_night")
    assert "fail_lowest_defense_count" not in bandit
    assert "fail_worst_building_delta" not in bandit
    assert set(bandit) == set(EFFECT_CONTRACTS["bandit_night"].consumed_fields)


def test_unshipped_rat_king_keeps_loot_bonus_frac():
    from app.domain.events import catastrophe_effect

    assert "rat_king" not in SHIPPED_CATASTROPHE_KEYS
    assert "loot_bonus_frac" in catastrophe_effect("rat_king")


def _resolve_ctx() -> CatastropheResolveCtx:
    updates: list[tuple] = []

    def update_event(eid, **kwargs):
        updates.append((eid, kwargs))

    return CatastropheResolveCtx(
        event_id=1,
        fiefs=[{"id": 1, "grain": 10, "goods": 10, "might": 5, "name": "A"}],
        event_actions=[],
        get_fief=lambda fid: None,
        update_fief=lambda *a, **k: None,
        update_event=update_event,
    )


def test_resolve_legacy_unknown_key_soft_closes():
    ctx = _resolve_ctx()
    text = resolve_catastrophe("flood", ctx)
    assert "завершилась" in text


def test_resolve_shipped_key_without_handler_raises(monkeypatch):
    monkeypatch.setattr(
        "app.domain.event_apply.SHIPPED_CATASTROPHE_KEYS",
        frozenset({"bandit_night", "cattle_plague", "ghost_shipped"}),
    )
    with pytest.raises(RuntimeError, match="ghost_shipped"):
        resolve_catastrophe("ghost_shipped", _resolve_ctx())
