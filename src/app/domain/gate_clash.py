"""Схватка у ворот: асимметричный налог крови без бегства (чистая логика)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app import balance as B
from app.domain.road_skirmish import deaths_from_loss_frac, _split_pool_proportional


@dataclass(frozen=True)
class GateClashResult:
    """Итог тела у ворот; applied=False если некого резать (нет дома и заставы)."""

    applied: bool
    attacker_deaths: int = 0
    defender_virtual_deaths: int = 0
    home_deaths: int = 0
    cover_deaths_total: int = 0
    cover_deaths_by_intent: dict[int, int] = field(default_factory=dict)

    def cover_refund(self, intent_id: int, deployed: int) -> int:
        dead = int(self.cover_deaths_by_intent.get(int(intent_id), 0))
        return max(0, int(deployed) - dead)


def pairwise_skirmish_deaths(attack: int, defense: int) -> tuple[int, int]:
    """Смерти (атака, защита) у ворот от слабой стороны, без бегства.

    База = min(атака, защита). Нападающий платит RAID_GATE_ATK_LOSS_FRAC,
    защита - RAID_GATE_DEF_LOSS_FRAC. Дорожный налог сюда не входит.
    """
    a = max(0, int(attack))
    d = max(0, int(defense))
    if a <= 0 or d <= 0:
        return 0, 0
    base = min(a, d)
    atk_dead = min(
        a, deaths_from_loss_frac(base, float(B.RAID_GATE_ATK_LOSS_FRAC))
    )
    def_dead = min(
        d, deaths_from_loss_frac(base, float(B.RAID_GATE_DEF_LOSS_FRAC))
    )
    return atk_dead, def_dead


def resolve_gate_clash(
    *,
    attack_pool: int,
    defense: int,
    home_might: int,
    cover_by_intent: dict[int, int],
) -> GateClashResult:
    """Защита = вес схватки; кровь только у живых (дом + застава), доли пропорциональны.

    Стены/дозор входят в defense и масштабируют виртуальные смерти вниз на fighters/D.
    """
    home = max(0, int(home_might))
    cover_clean = {
        int(iid): max(0, int(budget))
        for iid, budget in cover_by_intent.items()
        if max(0, int(budget)) > 0
    }
    cover_total = sum(cover_clean.values())
    if home <= 0 and cover_total <= 0:
        return GateClashResult(applied=False)

    defense_n = max(0, int(defense))
    atk_deaths, def_virtual = pairwise_skirmish_deaths(
        int(attack_pool), defense_n
    )
    fighters = home + cover_total
    if defense_n <= 0:
        applied_def = min(fighters, def_virtual)
    else:
        applied_def = min(
            fighters,
            int(round(def_virtual * fighters / defense_n)),
        )

    home_dead, cover_dead = _split_pool_proportional(
        [home, cover_total], applied_def
    )
    cover_deaths_by_intent: dict[int, int] = {}
    if cover_total > 0 and cover_dead > 0:
        intent_ids = sorted(cover_clean.keys())
        budgets = [cover_clean[i] for i in intent_ids]
        shares = _split_pool_proportional(budgets, int(cover_dead))
        cover_deaths_by_intent = {
            iid: int(share) for iid, share in zip(intent_ids, shares)
        }

    return GateClashResult(
        applied=True,
        attacker_deaths=int(atk_deaths),
        defender_virtual_deaths=int(def_virtual),
        home_deaths=int(home_dead),
        cover_deaths_total=int(cover_dead),
        cover_deaths_by_intent=cover_deaths_by_intent,
    )
