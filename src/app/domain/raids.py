"""Набеги, дозор, перехват."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from random import Random

from app import balance as B
from app.domain.resource_bags import LootBag, empty_loot_bag
from app.domain.resource_format import (
    format_attacker_loot_suffix,
    format_victim_loot_sentence,
)
from app.domain.resource_registry import raid_lootable_keys



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
class DeclareRaidResult:
    """Итог declare_raid для хендлера (без боя)."""

    intent_id: int
    victim_fief_id: int
    victim_name: str
    might: int
    men_home: int
    open_truce: bool
    lock_deadline_text: str
    resolve_slot_text: str
    pact_merge_hint: str | None = None
    dm_text: str = ""


@dataclass
class RaidNightPartyNotice:
    """Личка/группа после ночного разрешения."""

    user_id: int | None
    realm_id: int | None
    text: str
    kind: str  # "dm" | "public" | "continent"


@dataclass
class ResolveNightReport:
    resolved_count: int = 0
    notices: list[RaidNightPartyNotice] = field(default_factory=list)


@dataclass
class RaidActionResult:
    """Итог осады для ночных уведомлений (без Bot в движке)."""

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
    might_committed: int = 0
    might_lost: int = 0
    road_deaths: int = 0
    loss_rumor: str = ""

    def attacker_dm_text(self) -> str:
        """Личка нападающему: итог с суммами и слухом о своих потерях."""
        loss_bit = f" {self.loss_rumor}" if self.loss_rumor else ""
        road_bit = ""
        if self.road_deaths > 0:
            road_bit = " На дороге тоже потрепало."
        if self.success:
            return (
                f"Вы ограбили {self.victim_name}: "
                f"{format_attacker_loot_suffix(self.stolen)}."
                f"{loss_bit}{road_bit}"
            )
        if self.intercept_applied:
            return (
                f"Набег на хутор {self.victim_name} отбит "
                f"(союзник перехватил у ворот)."
                f"{loss_bit}{road_bit}"
            )
        return (
            f"Набег на хутор {self.victim_name} отбит у ворот."
            f"{loss_bit}{road_bit}"
        )

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


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def sample_raid_might_lost(
    attack_might: int,
    ratio: float,
    *,
    success: bool,
    rng: Random | None = None,
) -> int:
    """Потери дружины: колокол вокруг среднего от ratio; crush ≥ floor."""
    atk = max(0, int(attack_might))
    if atk <= 0:
        return 0
    rng = rng or Random()
    success_r = float(B.RAID_SUCCESS_R)
    overkill_r = float(B.RAID_LOOT_OVERKILL_R)
    if success:
        if overkill_r <= success_r:
            mean = float(B.RAID_CRUSH_LOSS_FLOOR)
        else:
            t = (float(ratio) - success_r) / (overkill_r - success_r)
            t = max(0.0, min(1.0, t))
            mean = _lerp(
                float(B.RAID_SUCCESS_LOSS_EDGE),
                float(B.RAID_CRUSH_LOSS_FLOOR),
                t,
            )
        mean = max(float(B.RAID_CRUSH_LOSS_FLOOR), mean)
    else:
        if success_r <= 0:
            mean = float(B.RAID_FAIL_LOSS_FLEE)
        else:
            t = max(0.0, min(1.0, float(ratio) / success_r))
            mean = _lerp(
                float(B.RAID_FAIL_LOSS_FLEE),
                float(B.RAID_FAIL_LOSS_NEAR),
                t,
            )
    frac = rng.gauss(mean, float(B.RAID_LOSS_SIGMA))
    frac = max(0.0, min(1.0, frac))
    if success:
        frac = max(float(B.RAID_CRUSH_LOSS_FLOOR), frac)
    if atk < int(B.RAID_MIN_MIGHT):
        return min(atk, max(0, int(round(atk * frac))))
    return min(atk, max(1, int(round(atk * frac))))


def own_loss_rumor_band(might_lost: int, commit: int) -> str:
    """Слух о своих потерях без точных цифр врага."""
    if commit <= 0 or might_lost <= 0:
        return "Свои почти все вернулись."
    frac = might_lost / commit
    if frac < 0.25:
        return "Свои потери лёгкие."
    if frac < 0.45:
        return "Свои потери чувствительные."
    return "Свои потери тяжёлые."


def own_headcount_rumor(returned: int, commit: int) -> str:
    """Грубая оценка вернувшихся своих."""
    if commit <= 0:
        return ""
    if returned <= 0:
        return "Домой почти никто не пришёл."
    frac = returned / commit
    if frac >= 0.75:
        return "Большая часть дружины вернулась."
    if frac >= 0.4:
        return "Около половины дружины вернулась."
    return "Домой пришла лишь малая часть."


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


def raid_loot_pool(
    stash: Mapping[str, int],
    barn_level: int,
    *,
    escrow: Mapping[str, int] | None = None,
    loot_keys: Sequence[str] | None = None,
) -> LootBag:
    """Незащищённый двор + открытый эскроу обозов (эскроу амбар не кроет)."""
    keys = tuple(loot_keys) if loot_keys is not None else raid_lootable_keys()
    pool = unprotected_stash(stash, barn_level, loot_keys=keys)
    if not escrow:
        return pool
    return {
        key: int(pool.get(key, 0) or 0)
        + max(0, int(escrow.get(key, 0) or 0))
        for key in keys
    }


def split_loot_prefer_escrow(
    stolen: Mapping[str, int],
    escrow: Mapping[str, int],
    *,
    loot_keys: Sequence[str] | None = None,
) -> tuple[LootBag, LootBag]:
    """Сначала груз у ворот, затем двор. Возвращает (from_escrow, from_stash)."""
    keys = tuple(loot_keys) if loot_keys is not None else raid_lootable_keys()
    from_escrow: LootBag = {}
    from_stash: LootBag = {}
    for key in keys:
        want = max(0, int(stolen.get(key, 0) or 0))
        gate = max(0, int(escrow.get(key, 0) or 0))
        take_escrow = min(want, gate)
        from_escrow[key] = take_escrow
        from_stash[key] = want - take_escrow
    return from_escrow, from_stash


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
    reinforce_might: int = 0,
) -> int:
    """Полная защита усадьбы (сторожка + дружина + дозор + застава/перехват)."""
    defense = float(watch_defense) + max(0, int(victim_might))
    if patrol_active and not fog_ignores_patrol:
        defense += B.PATROL_DEFENSE_BONUS
    reinforce = max(0, int(reinforce_might))
    if reinforce > 0:
        defense += float(reinforce) * float(B.COVER_DEFENSE_PER_MIGHT)
    elif intercept:
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
    escrow_stash: Mapping[str, int] | None = None,
    reinforce_might: int = 0,
    rng: Random | None = None,
) -> RaidResult:
    defense = standing_raid_defense(
        watch_defense=watch_defense,
        victim_might=victim_might,
        patrol_active=patrol_active,
        fog_ignores_patrol=fog_ignores_patrol,
        intercept=intercept,
        reinforce_might=reinforce_might,
    )
    loot_keys = raid_lootable_keys()
    zero_loot = {key: 0 for key in loot_keys}
    rng = rng or Random()

    r = raid_ratio(attack_might, defense)
    if r < B.RAID_SUCCESS_R:
        might_lost = sample_raid_might_lost(
            attack_might, r, success=False, rng=rng
        )
        return RaidResult(
            success=False,
            ratio=r,
            might_lost=might_lost,
            stolen=zero_loot,
            defense_used=defense,
            intercept_applied=intercept,
            public_line=f"Набег {attacker_name} на хутор {victim_name} отбит у ворот",
        )

    unprot = raid_loot_pool(
        victim_stash,
        barn_level,
        escrow=escrow_stash,
        loot_keys=loot_keys,
    )
    stolen = loot_amounts(
        r, unprot, victim_daily, loot_keys=loot_keys, rng=rng
    )
    might_lost = sample_raid_might_lost(
        attack_might, r, success=True, rng=rng
    )
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
