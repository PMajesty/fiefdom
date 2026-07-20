"""Modifier layer: composition, scope, duration, providers, equivalence to old readers."""
from __future__ import annotations

import inspect

import pytest

from app.domain.events import (
    SHIPPED_CATASTROPHE_KEYS,
    SHIPPED_MINOR_KEYS,
    catastrophe_effect,
    minor_effect,
)
from app.domain.modifiers import (
    COMPOSE_RULES,
    LIVE_READ_MODIFIER_KINDS,
    MODIFIER_SET_KIND_READERS,
    ActiveCatastropheRef,
    ComposeRule,
    EffectKind,
    Modifier,
    ModifierScope,
    ModifierSet,
    RealmModifierCtx,
    collect_active_modifiers,
    modifiers_from_catastrophe_key,
    modifiers_from_minor_key,
)
from app.engine import ENGINE_CONSUMED_MODIFIER_KINDS, Engine


def _legacy_minor_farm_mult(key: str | None) -> float:
    if not key:
        return 1.0
    try:
        eff = minor_effect(key)
    except KeyError:
        return 1.0
    if "farm_mult" not in eff:
        return 1.0
    return float(eff["farm_mult"])


def _legacy_catastrophe_farm_mult(key: str | None) -> float:
    if not key:
        return 1.0
    try:
        eff = catastrophe_effect(key)
    except KeyError:
        return 1.0
    if "farm_mult" not in eff:
        return 1.0
    return float(eff["farm_mult"])


def _legacy_upgrade(key: str | None) -> float:
    if not key:
        return 1.0
    try:
        return float(minor_effect(key).get("upgrade_cost_mult", 1.0))
    except KeyError:
        return 1.0


def _legacy_trade_bonus(key: str | None) -> float:
    if not key:
        return 0.0
    try:
        return float(minor_effect(key).get("trade_bonus_frac") or 0.0)
    except KeyError:
        return 0.0


def _legacy_fog(key: str | None) -> bool:
    if not key:
        return False
    try:
        return bool(minor_effect(key).get("raids_ignore_patrol"))
    except KeyError:
        return False


def _legacy_wedding(key: str | None) -> int:
    if not key:
        return 0
    try:
        return int(minor_effect(key).get("trade_gift_grain") or 0)
    except KeyError:
        return 0


def test_compose_driven_by_compose_rules_dict():
    """COMPOSE_RULES - единственный источник: смена правила меняет compose()."""
    mods = ModifierSet(
        (
            Modifier(EffectKind.FARM_MULT, ModifierScope.REALM, "a", 2.0),
            Modifier(EffectKind.FARM_MULT, ModifierScope.REALM, "b", 3.0),
        )
    )
    assert COMPOSE_RULES[EffectKind.FARM_MULT] is ComposeRule.MULTIPLY
    assert mods.compose(EffectKind.FARM_MULT) == pytest.approx(6.0)
    assert mods.farm_mult() == mods.compose(EffectKind.FARM_MULT)

    flag_mods = ModifierSet(
        (
            Modifier(EffectKind.FOG_IGNORES_PATROL, ModifierScope.REALM, "f", False),
            Modifier(EffectKind.FOG_IGNORES_PATROL, ModifierScope.REALM, "g", True),
        )
    )
    assert COMPOSE_RULES[EffectKind.FOG_IGNORES_PATROL] is ComposeRule.OR_FLAGS
    assert flag_mods.compose(EffectKind.FOG_IGNORES_PATROL) is True

    add_mods = ModifierSet(
        (
            Modifier(EffectKind.TRADE_BONUS_FRAC, ModifierScope.REALM, "t", 0.05),
            Modifier(EffectKind.TRADE_BONUS_FRAC, ModifierScope.REALM, "u", 0.02),
        )
    )
    assert COMPOSE_RULES[EffectKind.TRADE_BONUS_FRAC] is ComposeRule.ADD
    assert add_mods.compose(EffectKind.TRADE_BONUS_FRAC) == pytest.approx(0.07)


def test_modifier_set_multiplies_farm_and_upgrade():
    mods = ModifierSet(
        (
            Modifier(EffectKind.FARM_MULT, ModifierScope.REALM, "drought", 0.4375),
            Modifier(EffectKind.FARM_MULT, ModifierScope.REALM, "cattle_plague", 0.375),
            Modifier(EffectKind.UPGRADE_COST_MULT, ModifierScope.REALM, "good_stone", 0.75),
            Modifier(EffectKind.UPGRADE_COST_MULT, ModifierScope.REALM, "future", 0.5),
        )
    )
    assert mods.farm_mult() == pytest.approx(0.4375 * 0.375)
    assert mods.upgrade_cost_mult() == pytest.approx(0.75 * 0.5)


def test_modifier_set_or_flags_and_add_bonuses():
    mods = ModifierSet(
        (
            Modifier(EffectKind.FOG_IGNORES_PATROL, ModifierScope.REALM, "fog", False),
            Modifier(EffectKind.FOG_IGNORES_PATROL, ModifierScope.REALM, "fog2", True),
            Modifier(EffectKind.TRADE_BONUS_FRAC, ModifierScope.REALM, "fair", 0.05),
            Modifier(EffectKind.TRADE_BONUS_FRAC, ModifierScope.REALM, "fair2", 0.02),
            Modifier(EffectKind.TRADE_GIFT_GRAIN, ModifierScope.REALM, "wedding", 5),
            Modifier(EffectKind.TRADE_GIFT_GRAIN, ModifierScope.REALM, "wedding2", 3),
        )
    )
    assert mods.fog_ignores_patrol() is True
    assert mods.trade_bonus_frac() == pytest.approx(0.07)
    assert mods.trade_gift_grain() == 8


def test_scope_filtering_keeps_realm_and_matches_fief_target():
    mods = ModifierSet(
        (
            Modifier(EffectKind.FARM_MULT, ModifierScope.REALM, "drought", 0.5),
            Modifier(
                EffectKind.FARM_MULT, ModifierScope.FIEF, "local", 0.25, target_id=7
            ),
            Modifier(
                EffectKind.FARM_MULT, ModifierScope.FIEF, "other", 0.1, target_id=9
            ),
            Modifier(
                EffectKind.FOG_IGNORES_PATROL, ModifierScope.WORLD, "world_fog", True
            ),
        )
    )
    realm_only = mods.filter_scope(ModifierScope.REALM)
    assert realm_only.farm_mult() == 0.5
    assert realm_only.fog_ignores_patrol() is False

    fief7 = mods.filter_scope(ModifierScope.FIEF, target_id=7)
    assert fief7.farm_mult() == 0.25

    world = mods.filter_scope(ModifierScope.WORLD)
    assert world.fog_ignores_patrol() is True
    assert world.farm_mult() == 1.0


def test_exclude_expired_opt_in_drops_negative_not_live_compose():
    """exclude_expired - опциональный API; live farm_mult включает negative remaining."""
    mods = ModifierSet(
        (
            Modifier(
                EffectKind.FARM_MULT,
                ModifierScope.REALM,
                "overdue",
                0.5,
                ticks_remaining=-1,
            ),
            Modifier(
                EffectKind.FARM_MULT,
                ModifierScope.REALM,
                "last_tick",
                0.8,
                ticks_remaining=0,
            ),
            Modifier(
                EffectKind.FARM_MULT,
                ModifierScope.REALM,
                "open",
                0.5,
                ticks_remaining=None,
            ),
        )
    )
    assert mods.exclude_expired().farm_mult() == pytest.approx(0.8 * 0.5)
    assert mods.farm_mult() == pytest.approx(0.5 * 0.8 * 0.5)


def test_cattle_plague_ticks_remaining_from_resolves_tick():
    """resolves_tick=N при tick_index=M → ticks_remaining=N-M (метаданные); presence = apply."""
    mods = collect_active_modifiers(
        RealmModifierCtx(
            active_catastrophes=(
                ActiveCatastropheRef(key="cattle_plague", resolves_tick=10),
            ),
            tick_index=7,
        )
    )
    assert len(mods) == 1
    assert mods.modifiers[0].ticks_remaining == 3
    assert mods.farm_mult() == pytest.approx(0.375)

    on_resolve_tick = collect_active_modifiers(
        RealmModifierCtx(
            active_catastrophes=(
                ActiveCatastropheRef(key="cattle_plague", resolves_tick=10),
            ),
            tick_index=10,
        )
    )
    assert on_resolve_tick.modifiers[0].ticks_remaining == 0
    assert on_resolve_tick.farm_mult() == pytest.approx(0.375)

    past = collect_active_modifiers(
        RealmModifierCtx(
            active_catastrophes=(
                ActiveCatastropheRef(key="cattle_plague", resolves_tick=10),
            ),
            tick_index=11,
        )
    )
    assert past.modifiers[0].ticks_remaining == -1
    assert past.farm_mult() == pytest.approx(0.375)


@pytest.mark.parametrize("key", sorted(SHIPPED_MINOR_KEYS))
def test_provider_derives_minor_duration_from_table(key: str):
    mods = collect_active_modifiers(RealmModifierCtx(active_minor_key=key))
    eff = minor_effect(key)
    expected_duration = (
        int(eff["duration_ticks"]) if "duration_ticks" in eff else None
    )
    for mod in mods:
        assert mod.scope is ModifierScope.REALM
        assert mod.source_key == key
        assert mod.ticks_remaining == expected_duration


@pytest.mark.parametrize("key", sorted(SHIPPED_CATASTROPHE_KEYS))
def test_provider_derives_catastrophe_from_active_rows(key: str):
    mods = collect_active_modifiers(
        RealmModifierCtx(
            active_catastrophes=(ActiveCatastropheRef(key=key, resolves_tick=20),),
            tick_index=15,
        )
    )
    expected_kinds = set()
    if "farm_mult" in catastrophe_effect(key):
        expected_kinds.add(EffectKind.FARM_MULT)
    got_kinds = {m.kind for m in mods}
    assert got_kinds == expected_kinds
    for mod in mods:
        assert mod.ticks_remaining == 5


def test_provider_realistic_realm_rows_stack_drought_and_plague():
    mods = collect_active_modifiers(
        RealmModifierCtx(
            active_minor_key="drought",
            active_catastrophes=(
                ActiveCatastropheRef(key="cattle_plague", resolves_tick=12),
                ActiveCatastropheRef(key="bandit_night", resolves_tick=12),
            ),
            tick_index=10,
        )
    )
    assert mods.farm_mult() == pytest.approx(0.4375 * 0.375)
    assert mods.fog_ignores_patrol() is False


def test_overdue_active_cattle_plague_still_stacks_with_drought():
    """Active row с resolves_tick < tick_index всё ещё даёт farm_mult (storage presence)."""
    mods = collect_active_modifiers(
        RealmModifierCtx(
            active_minor_key="drought",
            active_catastrophes=(
                ActiveCatastropheRef(key="cattle_plague", resolves_tick=5),
            ),
            tick_index=6,
        )
    )
    assert mods.modifiers[-1].ticks_remaining == -1
    assert mods.farm_mult() == pytest.approx(0.1640625)
    assert mods.farm_mult() == pytest.approx(0.4375 * 0.375)


@pytest.mark.parametrize("key", sorted(SHIPPED_MINOR_KEYS))
def test_equivalence_shipped_minor_matches_legacy_readers(key: str):
    mods = collect_active_modifiers(RealmModifierCtx(active_minor_key=key))
    assert mods.farm_mult() == _legacy_minor_farm_mult(key)
    assert mods.upgrade_cost_mult() == _legacy_upgrade(key)
    assert mods.trade_bonus_frac() == _legacy_trade_bonus(key)
    assert mods.fog_ignores_patrol() is _legacy_fog(key)
    assert mods.trade_gift_grain() == _legacy_wedding(key)


@pytest.mark.parametrize("key", sorted(SHIPPED_CATASTROPHE_KEYS))
def test_equivalence_shipped_catastrophe_matches_legacy_readers(key: str):
    mods = collect_active_modifiers(
        RealmModifierCtx(active_catastrophes=(ActiveCatastropheRef(key=key),))
    )
    assert mods.farm_mult() == _legacy_catastrophe_farm_mult(key)
    assert mods.upgrade_cost_mult() == 1.0
    assert mods.trade_bonus_frac() == 0.0
    assert mods.fog_ignores_patrol() is False
    assert mods.trade_gift_grain() == 0


def test_equivalence_stacked_drought_cattle_plague():
    drought = _legacy_minor_farm_mult("drought")
    plague = _legacy_catastrophe_farm_mult("cattle_plague")
    mods = collect_active_modifiers(
        RealmModifierCtx(
            active_minor_key="drought",
            active_catastrophes=(ActiveCatastropheRef(key="cattle_plague"),),
        )
    )
    assert mods.farm_mult() == drought * plague


def test_modifiers_from_key_helpers_match_collect():
    assert modifiers_from_minor_key("fog") == collect_active_modifiers(
        RealmModifierCtx(active_minor_key="fog")
    ).modifiers
    assert modifiers_from_catastrophe_key("cattle_plague") == collect_active_modifiers(
        RealmModifierCtx(active_catastrophes=(ActiveCatastropheRef(key="cattle_plague"),))
    ).modifiers


def test_engine_reads_all_live_modifier_kinds():
    assert ENGINE_CONSUMED_MODIFIER_KINDS == LIVE_READ_MODIFIER_KINDS
    from app.services.caravans import CaravanService
    from app.services.land_actions import LandActionService
    from app.services.night_raids import NightRaidResolver
    from app.services.realm_tick import RealmTickRunner
    from app.services.world_tick import WorldTickOrchestrator

    src = (
        inspect.getsource(Engine)
        + inspect.getsource(RealmTickRunner)
        + inspect.getsource(NightRaidResolver)
        + inspect.getsource(WorldTickOrchestrator)
        + inspect.getsource(CaravanService)
        + inspect.getsource(LandActionService)
    )
    for kind in LIVE_READ_MODIFIER_KINDS:
        method = MODIFIER_SET_KIND_READERS[kind]
        assert f".{method}()" in src, (
            f"live path must read ModifierSet.{method}()"
        )
