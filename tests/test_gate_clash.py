"""Схватка у ворот: асимметричный налог, масштаб на живых."""
from __future__ import annotations

from app import balance as B
from app.domain.gate_clash import pairwise_skirmish_deaths, resolve_gate_clash
from app.domain.road_skirmish import deaths_from_loss_frac


def _gate_atk(base: int) -> int:
    return deaths_from_loss_frac(base, B.RAID_GATE_ATK_LOSS_FRAC)


def _gate_def(base: int) -> int:
    return deaths_from_loss_frac(base, B.RAID_GATE_DEF_LOSS_FRAC)


def test_gate_loss_fracs_are_regulable_and_asymmetric():
    assert B.RAID_GATE_ATK_LOSS_FRAC == 0.55
    assert B.RAID_GATE_DEF_LOSS_FRAC == 0.45
    assert B.RAID_GATE_ATK_LOSS_FRAC != B.RAID_ROAD_LOSS_FRAC
    assert B.RAID_GATE_DEF_LOSS_FRAC != B.RAID_ROAD_LOSS_FRAC


def test_pairwise_uses_weaker_side_with_asymmetric_fracs():
    atk, deff = pairwise_skirmish_deaths(40, 30)
    assert atk == _gate_atk(30)
    assert deff == _gate_def(30)
    assert atk > deff


def test_pairwise_no_flee_when_defense_under_half_attack():
    # На дороге 10 vs 40 ушли бы без крови; у ворот налог остаётся.
    atk, deff = pairwise_skirmish_deaths(40, 10)
    assert atk == _gate_atk(10)
    assert deff == _gate_def(10)
    assert atk > 0
    assert deff > 0


def test_pairwise_equal_asymmetric_tax():
    atk, deff = pairwise_skirmish_deaths(20, 20)
    assert atk == _gate_atk(20)
    assert deff == _gate_def(20)
    assert atk > deff


def test_pairwise_road_frac_unchanged_by_gate_knobs():
    """Дорога остаётся на RAID_ROAD_LOSS_FRAC (проверка константы, не боя)."""
    from app.domain.road_skirmish import _loser_deaths

    assert _loser_deaths(30) == max(1, round(30 * B.RAID_ROAD_LOSS_FRAC))
    assert _loser_deaths(30) != _gate_atk(30)


def test_gate_clash_scales_virtual_deaths_onto_fighters():
    # defense=80 (стены), живых 20; виртуальные смерти режутся долей fighters/D.
    gate = resolve_gate_clash(
        attack_pool=100,
        defense=80,
        home_might=20,
        cover_by_intent={},
    )
    assert gate.applied
    assert gate.attacker_deaths == _gate_atk(80)
    assert gate.defender_virtual_deaths == _gate_def(80)
    expected_home = min(20, int(round(gate.defender_virtual_deaths * 20 / 80)))
    assert gate.home_deaths == expected_home
    assert gate.cover_deaths_total == 0


def test_gate_clash_splits_home_and_cover_proportionally():
    # 20 дом + 50 застава, D>A: база 40 → def virtual по GATE_DEF; на живых 5+13.
    gate = resolve_gate_clash(
        attack_pool=40,
        defense=70,
        home_might=20,
        cover_by_intent={11: 50},
    )
    assert gate.applied
    assert gate.attacker_deaths == _gate_atk(40)
    assert gate.defender_virtual_deaths == _gate_def(40)
    assert gate.home_deaths == 5
    assert gate.cover_deaths_total == 13
    assert gate.cover_deaths_by_intent[11] == 13
    assert gate.cover_refund(11, 50) == 37


def test_gate_clash_skipped_without_living_fighters():
    gate = resolve_gate_clash(
        attack_pool=40,
        defense=25,
        home_might=0,
        cover_by_intent={},
    )
    assert not gate.applied
    assert gate.attacker_deaths == 0
    assert gate.home_deaths == 0


def test_gate_clash_cover_helpers_split_cover_pool():
    gate = resolve_gate_clash(
        attack_pool=40,
        defense=30,
        home_might=0,
        cover_by_intent={1: 20, 2: 10},
    )
    assert gate.applied
    assert gate.home_deaths == 0
    assert sum(gate.cover_deaths_by_intent.values()) == gate.cover_deaths_total
    assert gate.cover_deaths_by_intent[1] >= gate.cover_deaths_by_intent[2]

