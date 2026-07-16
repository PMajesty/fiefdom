"""Набеги, дозор, перехват."""
from __future__ import annotations

from dataclasses import dataclass

from app import balance as B


@dataclass
class RaidResult:
    success: bool
    ratio: float
    might_lost: int
    grain_stolen: int
    goods_stolen: int
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
    grain_stolen: int
    goods_stolen: int
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
                f"+{self.grain_stolen} зерна, +{self.goods_stolen} товаров."
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
                f"Унесено {self.grain_stolen} зерна и {self.goods_stolen} товаров."
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


def unprotected_stash(grain: int, goods: int, barn_level: int) -> tuple[int, int]:
    protect = B.barn_protect_frac(barn_level)
    ug = int(grain * (1.0 - protect))
    ugds = int(goods * (1.0 - protect))
    return max(0, ug), max(0, ugds)


def loot_amounts(
    ratio: float,
    unprot_grain: int,
    unprot_goods: int,
    victim_daily_grain: float,
    victim_daily_goods: float,
) -> tuple[int, int]:
    raw_g = ratio * B.RAID_LOOT_R_MULT * unprot_grain
    raw_d = ratio * B.RAID_LOOT_R_MULT * unprot_goods
    total_unprot = unprot_grain + unprot_goods
    desired = raw_g + raw_d
    if desired <= 0 or total_unprot <= 0:
        return 0, 0
    cap_frac = B.RAID_LOOT_MAX_FRAC * total_unprot
    cap_days = B.RAID_LOOT_MAX_DAYS_PROD * (victim_daily_grain + victim_daily_goods)
    scale = min(1.0, cap_frac / desired, (cap_days / desired) if desired else 1.0)
    g = min(int(raw_g * scale), unprot_grain)
    d = min(int(raw_d * scale), unprot_goods)
    return max(0, g), max(0, d)


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
    victim_grain: int,
    victim_goods: int,
    barn_level: int,
    victim_daily_grain: float,
    victim_daily_goods: float,
    fog_ignores_patrol: bool = False,
    victim_might: int = 0,
) -> RaidResult:
    defense = standing_raid_defense(
        watch_defense=watch_defense,
        victim_might=victim_might,
        patrol_active=patrol_active,
        fog_ignores_patrol=fog_ignores_patrol,
        intercept=intercept,
    )

    r = raid_ratio(attack_might, defense)
    if r < B.RAID_SUCCESS_R:
        return RaidResult(
            success=False,
            ratio=r,
            might_lost=attack_might,
            grain_stolen=0,
            goods_stolen=0,
            defense_used=defense,
            intercept_applied=intercept,
            public_line=f"Набег {attacker_name} на хутор {victim_name} отбит у ворот",
        )

    ug, ud = unprotected_stash(victim_grain, victim_goods, barn_level)
    g, d = loot_amounts(r, ug, ud, victim_daily_grain, victim_daily_goods)
    might_lost = max(1, int(round(attack_might * B.RAID_SUCCESS_MIGHT_LOSS_FRAC)))
    might_lost = min(attack_might, might_lost)
    return RaidResult(
        success=True,
        ratio=r,
        might_lost=might_lost,
        grain_stolen=g,
        goods_stolen=d,
        defense_used=defense,
        intercept_applied=intercept,
        # Суммы добычи только в личке сторон; в группе и в ночной сводке - без цифр.
        public_line=f"{attacker_name} ограбил {victim_name}",
    )
