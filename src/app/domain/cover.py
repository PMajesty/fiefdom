"""Застава: stance helpers, deploy caps, night settle."""
from __future__ import annotations

from dataclasses import dataclass, field

from app import balance as B

COVER_MODE_STAND_DOWN = "stand_down"
COVER_MODE_ANY = "any"
COVER_MODE_SPECIFIC = "specific"

COVER_MODE_LABELS = {
    COVER_MODE_STAND_DOWN: "Стоять в стороне",
    COVER_MODE_ANY: "Любого союзника",
    COVER_MODE_SPECIFIC: "Конкретного союзника",
}


@dataclass(frozen=True)
class CoverHelperOffer:
    fief_id: int
    intent_id: int
    mode: str
    budget: int
    user_id: int | None = None
    label: str = ""
    target_fief_id: int | None = None


@dataclass
class CoverDeployment:
    helpers: list[CoverHelperOffer] = field(default_factory=list)
    total: int = 0
    # (offer_slice, refund_amount) - не пошли в осаду (кап helpers/total)
    trimmed: list[tuple[CoverHelperOffer, int]] = field(default_factory=list)


def cover_matches_victim(
    mode: str, target_fief_id: int | None, victim_id: int
) -> bool:
    if mode == COVER_MODE_ANY:
        return True
    if mode == COVER_MODE_SPECIFIC:
        return target_fief_id is not None and int(target_fief_id) == int(victim_id)
    return False


def _priority_key(offer: CoverHelperOffer) -> tuple[int, int, int]:
    # specific > any; затем больший бюджет; стабильный id
    mode_rank = 0 if offer.mode == COVER_MODE_SPECIFIC else 1
    return (mode_rank, -int(offer.budget), int(offer.fief_id))


def filter_offers_for_victim(
    offers: list[CoverHelperOffer], *, victim_id: int
) -> list[CoverHelperOffer]:
    return [
        o
        for o in offers
        if int(o.budget) > 0
        and cover_matches_victim(o.mode, o.target_fief_id, victim_id)
    ]


def select_cover_deployment(
    offers: list[CoverHelperOffer],
    *,
    max_helpers: int | None = None,
    max_total: int | None = None,
) -> CoverDeployment:
    """Отбирает помощников: specific > any, затем бюджет; обрезка снизу приоритета.

    `offers` уже должны относиться к жертве (filter_offers_for_victim).
    `max_total` None = без потолка суммы (только лимит числа помощников).
    """
    max_helpers = int(
        B.COVER_MAX_HELPERS if max_helpers is None else max_helpers
    )
    matching = [o for o in offers if int(o.budget) > 0]
    matching.sort(key=_priority_key)

    kept: list[CoverHelperOffer] = []
    trimmed: list[tuple[CoverHelperOffer, int]] = []
    total = 0
    for offer in matching:
        if len(kept) >= max_helpers:
            trimmed.append((offer, int(offer.budget)))
            continue
        if max_total is None:
            deploy = int(offer.budget)
        else:
            room = int(max_total) - total
            if room <= 0:
                trimmed.append((offer, int(offer.budget)))
                continue
            deploy = min(int(offer.budget), room)
        if deploy <= 0:
            trimmed.append((offer, int(offer.budget)))
            continue
        leftover = int(offer.budget) - deploy
        kept.append(
            CoverHelperOffer(
                fief_id=offer.fief_id,
                intent_id=offer.intent_id,
                mode=offer.mode,
                budget=deploy,
                user_id=offer.user_id,
                label=offer.label,
                target_fief_id=offer.target_fief_id,
            )
        )
        total += deploy
        if leftover > 0:
            trimmed.append(
                (
                    CoverHelperOffer(
                        fief_id=offer.fief_id,
                        intent_id=offer.intent_id,
                        mode=offer.mode,
                        budget=leftover,
                        user_id=offer.user_id,
                        label=offer.label,
                        target_fief_id=offer.target_fief_id,
                    ),
                    leftover,
                )
            )
    return CoverDeployment(helpers=kept, total=total, trimmed=trimmed)


def format_cover_receipt_names(
    *,
    covered_labels: list[str],
    stood_down_labels: list[str],
) -> str:
    parts: list[str] = []
    if covered_labels:
        parts.append("У ворот стояли: " + ", ".join(covered_labels) + ".")
    if stood_down_labels:
        parts.append("В стороне: " + ", ".join(stood_down_labels) + ".")
    return " ".join(parts)
