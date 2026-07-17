"""Чистая логика дорожного боя и коалиций."""
from __future__ import annotations

from app import balance as B
from app.domain.road_skirmish import (
    RaidStack,
    build_coalitions,
    resolve_road_contest,
    split_loot_by_commit,
)


def test_pact_merge_and_truce_merge():
    stacks = [
        RaidStack(1, 10, 20, pact_id=5, open_truce=True),
        RaidStack(2, 11, 10, pact_id=5, open_truce=False),
        RaidStack(3, 12, 8, open_truce=True),
        RaidStack(4, 13, 7, open_truce=True),
        RaidStack(5, 14, 6, open_truce=False),
    ]
    coals = build_coalitions(stacks)
    keys = {c.key for c in coals}
    assert "pact:5" in keys
    assert "truce" in keys
    assert "solo:14" in keys
    pact = next(c for c in coals if c.key == "pact:5")
    assert pact.might == 30
    truce = next(c for c in coals if c.key == "truce")
    assert truce.might == 15


def test_flee_under_half_no_tax_on_leader():
    coals = build_coalitions(
        [
            RaidStack(1, 1, 40),
            RaidStack(2, 2, 10),
        ]
    )
    road = resolve_road_contest(coals, victim_label="B")
    assert road.siege_pool == 40
    fled = [f for f in road.member_fates if f.fled]
    assert len(fled) == 1
    assert fled[0].road_deaths == 0
    leader = [f for f in road.member_fates if f.siege_eligible]
    assert leader[0].road_deaths == 0


def test_skirmish_taxes_loser_and_shaves_winner():
    coals = build_coalitions(
        [
            RaidStack(1, 1, 40),
            RaidStack(2, 2, 30),
        ]
    )
    road = resolve_road_contest(coals)
    loser = next(f for f in road.member_fates if f.fief_id == 2)
    assert loser.road_deaths == max(1, round(30 * B.RAID_ROAD_LOSS_FRAC))
    assert road.siege_pool == 40 - loser.road_deaths
    winner = next(f for f in road.member_fates if f.fief_id == 1)
    assert winner.road_deaths == loser.road_deaths
    assert winner.siege_eligible


def test_tie_for_max_bounces_with_tax():
    coals = build_coalitions(
        [
            RaidStack(1, 1, 20),
            RaidStack(2, 2, 20),
        ]
    )
    road = resolve_road_contest(coals)
    assert road.siege_coalition_key is None
    assert road.siege_pool == 0
    for fate in road.member_fates:
        assert fate.road_deaths == max(1, round(20 * B.RAID_ROAD_LOSS_FRAC))
        assert not fate.siege_eligible


def test_loot_split_by_commit_remainder():
    shares = split_loot_by_commit(
        {1: 30, 2: 10},
        {"grain": 10, "goods": 3},
    )
    assert shares[1]["grain"] + shares[2]["grain"] == 10
    assert shares[1]["goods"] + shares[2]["goods"] == 3
    assert shares[1]["grain"] >= shares[2]["grain"]
