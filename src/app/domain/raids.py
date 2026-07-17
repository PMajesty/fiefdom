"""Набеги, дозор, перехват."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from random import Random

from app import balance as B
from app.domain.resources import (
    LootBag,
    empty_loot_bag,
    format_attacker_loot_suffix,
    format_victim_loot_sentence,
    raid_lootable_keys,
)


@dataclass
class RaidResult:
    success: bool
    ratio: float
    might_lost: int
    stolen: LootBag
    defense_used: int
    intercept_applied: bool
    public_line: str = ""


@dataclass
class RaidActionResult:
    """Итог engine.raid для хендлера (без Bot в движке)."""

    public_line: str
    success: bool
    victim_fief_id: int
    victim_user_id: int
    victim_name: str
    attacker_name: str
    stolen: LootBag = field(default_factory=empty_loot_bag)
    intercept_applied: bool = False
    interceptor_fief_id: int | None = None
    interceptor_user_id: int | None = None
    attacker_realm_id: int = 0
    victim_realm_id: int = 0
    via_portal: bool = False
    attacker_public_line: str = ""
    victim_public_line: str = ""

    def attacker_dm_text(self) -> str:
        """Личка нападающему: итог с суммами. В группу суммы не идут."""
        if self.success:
            return (
                f"Вы ограбили {self.victim_name}: "
                f"{format_attacker_loot_suffix(self.stolen)}"
            )
        if self.intercept_applied:
            return (
                f"Набег на хутор {self.victim_name} отбит "
                f"(союзник перехватил у ворот)."
            )
        return f"Набег на хутор {self.victim_name} отбит у ворот."

    def victim_dm_text(self) -> str:
        if self.success:
            return (
                f"На ваш хутор напал {self.attacker_name}! "
                f"{format_victim_loot_sentence(self.stolen)}"
            )
        if self.intercept_applied:
            return (
                f"Набег {self.attacker_name} на ваш хутор отбит "
                f"(союзник перехватил у ворот)."
            )
        return f"Набег {self.attacker_name} на ваш хутор отбит у ворот."

    def interceptor_dm_text(self) -> str | None:
        if not self.intercept_applied or self.interceptor_user_id is None:
            return None
        if self.success:
            return (
                f"Перехват не спас хутор {self.victim_name}: "
                f"{self.attacker_name} всё же ушёл с добычей."
            )
        return f"Вы перехватили набег {self.attacker_name} на хутор {self.victim_name}."


def raid_ratio(attack_might: int, defense: int) -> float:
    s = max(0, attack_might)
    d = max(0, defense)
    if s + d <= 0:
        return 0.0
    return s / (s + d)


def unprotected_stash(
    stash: Mapping[str, int],
    barn_level: int,
    *,
    loot_keys: Sequence[str] | None = None,
) -> LootBag:
    keys = tuple(loot_keys) if loot_keys is not None else raid_lootable_keys()
    protect = B.barn_protect_frac(barn_level)
    return {
        key: max(0, int(int(stash.get(key, 0) or 0) * (1.0 - protect)))
        for key in keys
    }


def loot_overkill_factor(ratio: float) -> float:
    """1.0 при сильном перевесе; RAID_LOOT_EDGE_FACTOR у порога успеха."""
    lo = float(B.RAID_SUCCESS_R)
    hi = float(B.RAID_LOOT_OVERKILL_R)
    if hi <= lo:
        return 1.0
    t = (float(ratio) - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    edge = float(B.RAID_LOOT_EDGE_FACTOR)
    return edge + (1.0 - edge) * t


def loot_amounts(
    ratio: float,
    unprot: Mapping[str, int],
    daily: Mapping[str, float],
    *,
    loot_keys: Sequence[str] | None = None,
    rng: Random | None = None,
) -> LootBag:
    """Добыча по lootable-ключам. Для grain/goods числа совпадают с прежней формулой."""
    keys = tuple(loot_keys) if loot_keys is not None else raid_lootable_keys()
    rng = rng or Random()
    swing = rng.uniform(float(B.RAID_LOOT_RND_MIN), float(B.RAID_LOOT_RND_MAX))
    factor = loot_overkill_factor(ratio) * swing
    raw = {
        key: ratio * B.RAID_LOOT_R_MULT * int(unprot.get(key, 0) or 0) * factor
        for key in keys
    }
    total_unprot = sum(int(unprot.get(key, 0) or 0) for key in keys)
    desired = sum(raw.values())
    if desired <= 0 or total_unprot <= 0:
        return {key: 0 for key in keys}
    cap_frac = B.RAID_LOOT_MAX_FRAC * total_unprot
    daily_sum = sum(float(daily.get(key, 0) or 0) for key in keys)
    cap_days = B.RAID_LOOT_MAX_DAYS_PROD * daily_sum
    scale = min(1.0, cap_frac / desired, (cap_days / desired) if desired else 1.0)
    out: LootBag = {
        key: max(0, min(int(raw[key] * scale), int(unprot.get(key, 0) or 0)))
        for key in keys
    }
    # У порога int() часто обнуляет всё; успех при ненулевом складе даёт кроху.
    # При равном unprot побеждает более ранний ключ реестра (зерно перед товарами).
    if sum(out.values()) == 0 and total_unprot > 0:
        best_key: str | None = None
        best_unprot = -1
        for key in keys:
            u = int(unprot.get(key, 0) or 0)
            if u > best_unprot:
                best_unprot = u
                best_key = key
        if best_key is not None and best_unprot > 0:
            out[best_key] = 1
    return out


def standing_raid_defense(
    *,
    watch_defense: float,
    victim_might: int,
    patrol_active: bool,
    fog_ignores_patrol: bool = False,
    intercept: bool = False,
) -> int:
    """Полная защита усадьбы в формуле набега (сторожка + дружина + дозор + перехват)."""
    defense = float(watch_defense) + max(0, int(victim_might))
    if patrol_active and not fog_ignores_patrol:
        defense += B.PATROL_DEFENSE_BONUS
    if intercept:
        defense += B.INTERCEPT_DEFENSE
    return int(defense)


def resolve_raid(
    *,
    attacker_name: str,
    victim_name: str,
    attack_might: int,
    watch_defense: float,
    patrol_active: bool,
    intercept: bool,
    victim_stash: Mapping[str, int],
    barn_level: int,
    victim_daily: Mapping[str, float],
    fog_ignores_patrol: bool = False,
    victim_might: int = 0,
    rng: Random | None = None,
) -> RaidResult:
    defense = standing_raid_defense(
        watch_defense=watch_defense,
        victim_might=victim_might,
        patrol_active=patrol_active,
        fog_ignores_patrol=fog_ignores_patrol,
        intercept=intercept,
    )
    loot_keys = raid_lootable_keys()
    zero_loot = {key: 0 for key in loot_keys}

    r = raid_ratio(attack_might, defense)
    if r < B.RAID_SUCCESS_R:
        return RaidResult(
            success=False,
            ratio=r,
            might_lost=attack_might,
            stolen=zero_loot,
            defense_used=defense,
            intercept_applied=intercept,
            public_line=f"Набег {attacker_name} на хутор {victim_name} отбит у ворот",
        )

    unprot = unprotected_stash(victim_stash, barn_level, loot_keys=loot_keys)
    stolen = loot_amounts(
        r, unprot, victim_daily, loot_keys=loot_keys, rng=rng
    )
    might_lost = max(1, int(round(attack_might * B.RAID_SUCCESS_MIGHT_LOSS_FRAC)))
    might_lost = min(attack_might, might_lost)
    return RaidResult(
        success=True,
        ratio=r,
        might_lost=might_lost,
        stolen=stolen,
        defense_used=defense,
        intercept_applied=intercept,
        # Суммы добычи только в личке сторон; в группе и в ночной сводке - без цифр.
        public_line=f"{attacker_name} ограбил {victim_name}",
    )
