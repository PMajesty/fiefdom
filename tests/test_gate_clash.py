"""Схватка у ворот: дорожный налог без бегства, масштаб на живых."""
from __future__ import annotations

from app import balance as B
from app.domain.gate_clash import pairwise_skirmish_deaths, resolve_gate_clash
from app.domain.road_skirmish import _loser_deaths


def test_pairwise_matches_road_loser_tax_and_mirror():
    atk, deff = pairwise_skirmish_deaths(40, 30)
    lost = _loser_deaths(30)
    assert lost == max(1, round(30 * B.RAID_ROAD_LOSS_FRAC))
    assert atk == lost
    assert deff == lost


def test_pairwise_no_flee_when_defense_under_half_attack():
    # На дороге 10 vs 40 ушли бы без крови; у ворот налог остаётся.
    atk, deff = pairwise_skirmish_deaths(40, 10)
    assert deff == _loser_deaths(10)
    assert atk == deff
    assert atk > 0


def test_pairwise_equal_both_pay_tax():
    atk, deff = pairwise_skirmish_deaths(20, 20)
    tax = _loser_deaths(20)
    assert atk == tax
    assert deff == tax


def test_gate_clash_scales_virtual_deaths_onto_fighters():
    # defense=80 (стены), живых 20; виртуальные смерти режутся долей fighters/D.
    gate = resolve_gate_clash(
        attack_pool=100,
        defense=80,
        home_might=20,
        cover_by_intent={},
    )
    assert gate.applied
    virtual = _loser_deaths(80)
    assert gate.defender_virtual_deaths == virtual
    assert gate.attacker_deaths == virtual
    expected_home = min(20, int(round(virtual * 20 / 80)))
    assert gate.home_deaths == expected_home
    assert gate.cover_deaths_total == 0


def test_gate_clash_splits_home_and_cover_proportionally():
    # 20 дом + 50 застава при D>A: налог 10 с атаки, на живых 3+7.
    gate = resolve_gate_clash(
        attack_pool=40,
        defense=70,
        home_might=20,
        cover_by_intent={11: 50},
    )
    assert gate.applied
    assert gate.attacker_deaths == _loser_deaths(40)
    assert gate.defender_virtual_deaths == gate.attacker_deaths
    assert gate.home_deaths == 3
    assert gate.cover_deaths_total == 7
    assert gate.cover_deaths_by_intent[11] == 7
    assert gate.cover_refund(11, 50) == 43


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
