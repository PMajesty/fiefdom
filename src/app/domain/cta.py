"""Primary CTA heuristic and raid/pact unlock gate for the fief home UI."""
from __future__ import annotations

from app import balance as B


def raid_pact_unlocked(*, onboard_step: int, day_number: int) -> bool:
    """Набег/Пакт в UI: квесты закрыты (onboard_step >= 4) и день долины >= RAID_PACT_UNLOCK_DAY."""
    return int(onboard_step) >= 4 and int(day_number) >= int(B.RAID_PACT_UNLOCK_DAY)


def choose_primary_cta(
    fief_id: int,
    *,
    actions: int,
    onboard_step: int,
    tile_count: int = 2,
    goods: int = 0,
    might: int = 0,
    day_number: int = B.RAID_PACT_UNLOCK_DAY,
    min_build_cost: int | None = None,
    next_claim_cost: int | None = None,
) -> tuple[str, str]:
    """Эвристика \"что делать сейчас\": (подпись кнопки, callback_data).

    Набег в primary CTA только после unlock (квесты + день долины).
    Не предлагает стройку/клейм, если товаров заведомо не хватает.
    """
    fid = int(fief_id)
    actions = int(actions)
    onboard_step = int(onboard_step)
    tile_count = int(tile_count)
    goods = int(goods)
    might = int(might)
    day_number = int(day_number)
    unlocked = raid_pact_unlocked(
        onboard_step=onboard_step, day_number=day_number
    )

    if next_claim_cost is None and tile_count < B.TILE_HARD_CAP:
        try:
            next_claim_cost = B.claim_cost(tile_count + 1)
        except ValueError:
            next_claim_cost = None

    can_claim = (
        actions > 0
        and next_claim_cost is not None
        and goods >= int(next_claim_cost)
    )
    if min_build_cost is not None:
        can_build = actions > 0 and goods >= int(min_build_cost)
    else:
        can_build = actions > 0 and goods >= 20

    if actions > 0 and onboard_step == 2:
        if can_claim:
            return "Квест: занять землю", f"clm:{fid}"
        return "Передать", f"snd:{fid}"
    if actions > 0 and onboard_step == 3:
        if can_build:
            return "Квест: строить", f"bld:{fid}"
        return "Передать", f"snd:{fid}"
    if actions > 0:
        if tile_count < 3:
            return "Занять землю", f"clm:{fid}"
        if can_build:
            return "Строить", f"bld:{fid}"
        if unlocked and might >= 5:
            return "Набег", f"rad:{fid}"
        return "Занять землю", f"clm:{fid}"
    return "Передать", f"snd:{fid}"
