"""Все числа баланса и таблицы контента. Тюнинг ≠ деплой."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --- Карта ---
TILES_PER_PLAYER = 9
MAP_MIN_TILES = 36  # 6×6
MAP_MAX_TILES = 100
MAP_GROWTH_CLAIMED_RATIO = 0.70

# Топология долин (рёбра realm_links). Play пока continent-wide; лимит - на create.
MAX_REALM_NEIGHBORS = 3

TILE_FIELD = "field"
TILE_FOREST = "forest"
TILE_HILLS = "hills"
TILE_RIVER = "river"
TILE_ROAD = "road"
TILE_RUINS = "ruins"
TILE_WILDS = "wilds"

TILE_EMOJI = {
    TILE_FIELD: "🌾",
    TILE_FOREST: "🌲",
    TILE_HILLS: "⛰️",
    TILE_RIVER: "🌊",
    TILE_ROAD: "🛤️",
    TILE_RUINS: "🕳️",
    TILE_WILDS: "🪴",
}

TILE_NAMES_RU = {
    TILE_FIELD: "Поле",
    TILE_FOREST: "Лес",
    TILE_HILLS: "Холмы",
    TILE_RIVER: "Река",
    TILE_ROAD: "Дорога",
    TILE_RUINS: "Руины",
    TILE_WILDS: "Глушь",
}

# Веса заполнения (дорога/река кладутся отдельно).
TILE_FILL_WEIGHTS = {
    TILE_FIELD: 30,
    TILE_FOREST: 20,
    TILE_HILLS: 15,
    TILE_RUINS: 8,
    TILE_WILDS: 27,
}
TILE_CLUSTER_BONUS = 0.15

RIVER_PASSIVE_GRAIN = 9
ROAD_PASSIVE_GOODS = 6
# Базовый доход усадьбы (не от зданий): соло-старт без рынка не зависает.
FIEF_BASE_GOODS = 5
RUINS_LOOT_MIN = 40
RUINS_LOOT_MAX = 50
# После находки руины дают постоянный двойной пассив (не ×1.5 native).
RUINS_PASSIVE_GRAIN = 5
RUINS_PASSIVE_GOODS = 5
# Стартовая усадьба не на руинах и не рядом (тор Манхэттен < этого порога).
RUINS_SPAWN_MIN_DISTANCE = 2
WILDS_CLAIM_MULT = 1
WILDS_CLEAR_TO = (TILE_FIELD, TILE_FOREST, TILE_HILLS)

# --- Усадьба / клетки ---
TILE_HARD_CAP = 9
CLAIM_COSTS = {
    2: 20,
    3: 45,
    4: 90,
    5: 150,
    6: 220,
    7: 360,
    8: 520,
    9: 700,
}
# Потолок после множителя глуши: клетка не дороже склада амбара III.
CLAIM_COST_HARD_CAP = 800
# Старая кривая (для ручного рефанда на VPS). Не менять.
OLD_CLAIM_COSTS_V1 = {
    2: 20,
    3: 60,
    4: 120,
    5: 250,
    6: 400,
    7: 600,
    8: 850,
    9: 1150,
}
CORE_TILE_FLOOR = 2  # после второго клейма; до него защищена только стартовая

# --- Ресурсы ---
RES_GRAIN = "grain"
RES_GOODS = "goods"
RES_MIGHT = "might"

# Отображаемые имена и tradeable - в domain.resource_registry.

DEFAULT_STASH_CAP = 150  # зерно/товары без амбара
COLLECT_CAP_DAYS_BASE = 3

STARTING_GRAIN = 30
STARTING_GOODS = 30
STARTING_MIGHT = 5
STARTING_MANOR_LEVEL = 1

# Разовый сбор ресурса за 1 действие (плоско, без зданий).
GATHER_AMOUNTS: dict[str, int] = {
    RES_GRAIN: 12,
    RES_GOODS: 10,
    RES_MIGHT: 2,
}
GATHER_GRAIN = GATHER_AMOUNTS[RES_GRAIN]
GATHER_GOODS = GATHER_AMOUNTS[RES_GOODS]
GATHER_MIGHT = GATHER_AMOUNTS[RES_MIGHT]

# Снос: доля возврата от суммы базовых затрат уровней 1..N.
DEMOLISH_REFUND_FRAC = 0.66

# --- Содержание ---
def land_upkeep(tile_count: int) -> int:
    return 4 + 2 * max(0, tile_count - 1)


MILITIA_FREE = 5
MILITIA_GRAIN_PER_EXCESS = 0.5  # ceil на тике

HUNGER_PRODUCTION_MULT = 0.5

ACTIONS_PER_DAY = 1
ACTIONS_BANK_MAX = 5

# --- Здания ---
BLD_MANOR = "manor"
BLD_FARM = "farm"
BLD_WORKSHOP = "workshop"
BLD_WATCH = "watchtower"
BLD_BARN = "barn"

BUILDING_NAMES_RU = {
    BLD_MANOR: "Двор",
    BLD_FARM: "Ферма",
    BLD_WORKSHOP: "Мастерская",
    BLD_WATCH: "Сторожка",
    BLD_BARN: "Амбар",
}

# Строятся игроком (двор ставится только при основании усадьбы).
PLAYER_BUILDINGS = (BLD_FARM, BLD_WORKSHOP, BLD_WATCH, BLD_BARN)

NATIVE_TILE = {
    BLD_MANOR: None,
    BLD_FARM: TILE_FIELD,
    BLD_WORKSHOP: TILE_FOREST,
    BLD_WATCH: TILE_HILLS,
    BLD_BARN: None,
}

NATIVE_BONUS = 1.5

BUILDING_COSTS = {
    BLD_FARM: {1: 20, 2: 50, 3: 120},
    BLD_WORKSHOP: {1: 25, 2: 60, 3: 140},
    BLD_WATCH: {1: 20, 2: 50, 3: 110},
    BLD_BARN: {1: 30, 2: 70, 3: 150},
}

# Главная клетка (двор): меньше зерна чем ферма I, больше товаров, сила до free cap.
MANOR_GRAIN = 5
MANOR_GOODS = 8
MANOR_MIGHT = 2

FARM_YIELD = {1: 8, 2: 14, 3: 22}
WORKSHOP_YIELD = {1: 5, 2: 9, 3: 14}
WATCH_DEFENSE = {1: 6, 2: 12, 3: 20}
WATCH_MIGHT = {1: 2, 2: 4, 3: 6}
BARN_CAP = {1: 250, 2: 450, 3: 800}
BARN_PROTECT = {1: 0.25, 2: 0.40, 3: 0.60}
BARN_COLLECT_BONUS_DAYS = 1  # за каждый уровень амбара
CLAIM_STASH_TOO_SMALL = (
    "Склад слишком мал для этой клетки - нужен больший амбар"
)

REPAIR_COST_MULT = 0.5  # от стоимости апгрейда на этот уровень

# --- Набеги ---
RAID_MIN_MIGHT = 5
RAID_SUCCESS_R = 0.25
RAID_LOOT_R_MULT = 0.88
RAID_LOOT_MAX_FRAC = 0.66
RAID_LOOT_MAX_DAYS_PROD = 3
# Добыча: у порога успеха тонкая, к overkill (ratio) выходит на полную.
RAID_LOOT_OVERKILL_R = 0.75
RAID_LOOT_EDGE_FACTOR = 0.40
# Случайный разброс добычи после учёта overkill.
RAID_LOOT_RND_MIN = 0.85
RAID_LOOT_RND_MAX = 1.15
# Потери дружины: колокол вокруг среднего от ratio (см. sample_raid_might_lost).
RAID_CRUSH_LOSS_FLOOR = 0.18
RAID_SUCCESS_LOSS_EDGE = 0.55
RAID_FAIL_LOSS_FLEE = 0.18
RAID_FAIL_LOSS_NEAR = 0.40
RAID_LOSS_SIGMA = 0.10
# Дорожный бой перед осадой (соперники на одной жертве).
RAID_ROAD_FLEE_FRAC = 0.5
RAID_ROAD_LOSS_FRAC = 0.25
# Схватка у ворот: доля от слабой стороны (atk/def раздельно, дорогу не трогает).
RAID_GATE_ATK_LOSS_FRAC = 0.55
RAID_GATE_DEF_LOSS_FRAC = 0.45
# Длительности в тиках долины (4 тика/день: 10:00, 13:00, 16:00 и 19:00).
# Глобальный щит жертвы: после удачного набега никто не бьёт её N тик(ов).
RAID_VICTIM_SHIELD_TICKS = 1
RAID_SAME_VICTIM_TICKS = 1

PATROL_COST_MIGHT = 0
PATROL_DEFENSE_BONUS = 18
PATROL_TICKS = 2

INTERCEPT_MIGHT = 5
INTERCEPT_DEFENSE = 5

# --- Застава (standing night cover пакта) ---
COVER_BUDGET_MIN = 5
COVER_MAX_HELPERS = 3
COVER_DEFENSE_PER_MIGHT = 1.0

FEUD_RAIDS_IN_WINDOW = 3
FEUD_WINDOW_TICKS = 14

PACT_SIZE_MIN = 2
PACT_SIZE_MAX = 5
PACT_INVITE_EXPIRE_TICKS = 4

# --- Слухи (капельные сплетни между тиками) ---
# Волн на окно play: 0 / 1 (base=0 в rumor_count_for_window).
RUMOR_WINDOW_COUNT_WEIGHTS = (0.60, 0.40)
RUMOR_QUIET_START_HOUR = 21
RUMOR_QUIET_END_HOUR = 8
RUMOR_ARCHIVE_MAX = 12
RUMOR_WEALTH_BANDS = (40, 120, 300)  # границы: тощая / сытая / тугая / ломится
RUMOR_MIGHT_BANDS = (8, 20)  # тонка / крепкая / много копий
# Доля строк-предвестий события, когда hint есть.
RUMOR_EVENT_LINE_CHANCE = 0.55
# За сколько тиков до беды рынок начинает шептать о катастрофе.
RUMOR_CATASTROPHE_WARN_TICKS = 4

# --- Караваны (передача на доверии) ---
# Крупный обоз виден долинам континента при выезде и при прибытии.
CARAVAN_PUBLIC_AMOUNT = 30
# TRADEABLE - флаг ResourceDef.tradeable в domain.resource_registry

# --- События ---
MINOR_EVENT_CHANCE = 1.0
CATASTROPHE_MIN_TICKS = 10
CATASTROPHE_MAX_TICKS = 16
CATASTROPHE_GAP_TICKS = 8
CATASTROPHE_WINDOW_TICKS_MIN = 1
CATASTROPHE_WINDOW_TICKS_MAX = 2

BANDIT_NIGHT_MIGHT_PER_PLAYER = 3.0
BANDIT_NIGHT_LOOT_PER_PLAYER = 12
BANDIT_NIGHT_FAIL_GRAIN_FRAC = 0.3125

# --- Отсутствие (тики без активности игрока) ---
DORMANT_TICKS = 14
OVERGROWN_TICKS = 42
OVERGROWN_COMPENSATION = 0.5

# --- Онбординг ---
# шаг 2 (занять землю) → товары; шаг 3 (стройка) → зерно; готово при >= 4
ONBOARD_DAY2_GOODS = 15
ONBOARD_DAY3_GOODS = 15

# Набег/Пакт в UI: после квестов (onboard_step >= 4) и с этого дня долины.
RAID_PACT_UNLOCK_DAY = 3

# Feature flags по умолчанию
DEFAULT_FEATURE_FLAGS = {
    "relics": False,
    "shrine": False,
    "gold": False,
}


@dataclass
class RealmBalanceOverride:
    """Переопределения чисел на уровне долины (JSON в БД)."""

    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any) -> Any:
        return self.data.get(key, default)


def claim_cost(next_tile_count: int, is_wilds: bool = False) -> int:
    base = CLAIM_COSTS.get(next_tile_count)
    if base is None:
        raise ValueError(f"Нельзя претендовать на клетку №{next_tile_count}")
    raw = base * (WILDS_CLAIM_MULT if is_wilds else 1)
    return min(int(raw), CLAIM_COST_HARD_CAP)


def claim_cost_refund_delta(active_tile_count: int) -> int:
    """Сколько товаров вернуть усадьбе с N активными (не заросшими) клетками.

    Только разница базовых кривых OLD_CLAIM_COSTS_V1 → CLAIM_COSTS.
    Старый множитель глуши (×2) в истории оплаты не учитывается:
    в БД нет состава клеток на момент каждого клейма.
    """
    n = int(active_tile_count)
    if n < 2:
        return 0
    total = 0
    for tile_n in range(2, min(n, TILE_HARD_CAP) + 1):
        old = OLD_CLAIM_COSTS_V1.get(tile_n)
        new = CLAIM_COSTS.get(tile_n)
        if old is None or new is None:
            continue
        total += max(0, int(old) - int(new))
    return total


def stash_cap(barn_level: int) -> int:
    if barn_level <= 0:
        return DEFAULT_STASH_CAP
    return BARN_CAP.get(barn_level, DEFAULT_STASH_CAP)


def claim_fits_stash(cost: int, barn_level: int) -> bool:
    return int(cost) <= stash_cap(barn_level)


def claim_stash_gate_message(cost: int, barn_level: int) -> str | None:
    """None если занятие по складу доступно; иначе текст блокировки."""
    cap = stash_cap(barn_level)
    if int(cost) <= cap:
        return None
    return (
        f"{CLAIM_STASH_TOO_SMALL} "
        f"(нужно {int(cost)} тов., склад до {cap})."
    )


def barn_protect_frac(barn_level: int) -> float:
    if barn_level <= 0:
        return 0.0
    return BARN_PROTECT.get(barn_level, 0.0)


def collect_cap_days(barn_level: int) -> int:
    return COLLECT_CAP_DAYS_BASE + max(0, barn_level) * BARN_COLLECT_BONUS_DAYS


def militia_upkeep_grain(might: int) -> int:
    """Сколько зерна в день нужно на дружину (округление вверх)."""
    import math

    excess = max(0, int(might) - MILITIA_FREE)
    if excess <= 0:
        return 0
    return int(math.ceil(excess * MILITIA_GRAIN_PER_EXCESS))


def militia_billable_might(might: int, prepaid_might: int = 0) -> int:
    """Сила без prepaid-возврата с похода (на неё только и идёт утреннее жалование)."""
    total = max(0, int(might))
    prepaid = max(0, min(int(prepaid_might), total))
    return total - prepaid


def travel_supply_grain(might: int) -> int:
    """Разовое снабжение похода/заставы: ceil(B × ставка), без бесплатной полосы."""
    import math

    force = max(0, int(might))
    if force <= 0:
        return 0
    return int(math.ceil(force * MILITIA_GRAIN_PER_EXCESS))


def militia_affordable(might: int, grain_available: int) -> int:
    """Максимум силы, который можно прокормить при данном зерне на жалование."""
    import math

    if grain_available < 0:
        grain_available = 0
    # free band always kept if we had them; disband only excess we can't pay
    max_excess = int(math.floor(grain_available / MILITIA_GRAIN_PER_EXCESS)) if MILITIA_GRAIN_PER_EXCESS else 10**9
    return MILITIA_FREE + max_excess


def militia_keep_after_shortfall(
    might: int,
    paid_grain: int,
    need_grain: int,
    *,
    prepaid_might: int = 0,
) -> tuple[int, int]:
    """Недоплата жалования: (новая сила, сколько разошлось).

    Prepaid с похода не режется. Бесплатная полоса действует на billable
    (домашних), не на prepaid.
    """
    total = max(0, int(might))
    prepaid = max(0, min(int(prepaid_might), total))
    billable = total - prepaid
    if int(paid_grain) >= int(need_grain) or billable <= 0:
        return total, 0
    keep_billable = min(billable, militia_affordable(billable, int(paid_grain)))
    if int(paid_grain) <= 0:
        keep_billable = min(billable, MILITIA_FREE)
    disbanded = billable - keep_billable
    return prepaid + keep_billable, disbanded


def militia_after_disband(might: int, keep: int) -> tuple[int, int]:
    """Добровольный роспуск: (новая сила, сколько ушло). keep в [0, might]."""
    current = max(0, int(might))
    target = max(0, min(int(keep), current))
    return target, current - target


def building_upgrade_cost(building: str, target_level: int) -> int:
    costs = BUILDING_COSTS[building]
    if target_level not in costs:
        raise ValueError(f"Нет уровня {target_level} для {building}")
    return costs[target_level]


def building_invested_goods(building: str, level: int) -> int:
    """Сумма базовых затрат уровней 1..level (без скидок событий)."""
    if building not in BUILDING_COSTS or level <= 0:
        return 0
    costs = BUILDING_COSTS[building]
    return sum(int(costs[lv]) for lv in range(1, int(level) + 1) if lv in costs)


def demolish_refund_goods(building: str, level: int) -> int:
    invested = building_invested_goods(building, level)
    return int(invested * DEMOLISH_REFUND_FRAC)


def repair_cost(building: str, level_to_restore: int) -> int:
    """Стоимость починки до level_to_restore (= апгрейд на этот уровень × 0.5)."""
    return int(building_upgrade_cost(building, level_to_restore) * REPAIR_COST_MULT)


def scaled_building_cost(base: int, cost_mult: float = 1.0) -> int:
    if cost_mult == 1.0:
        return int(base)
    return int(base * cost_mult)


def build_action_cost(
    building: str,
    tile: dict,
    *,
    cost_mult: float = 1.0,
) -> int | None:
    """Стоимость постройки/апгрейда/ремонта выбранного типа на клетке, или None."""
    if building not in PLAYER_BUILDINGS:
        return None
    current = tile.get("building")
    level = int(tile.get("building_level") or 0)
    damaged = bool(tile.get("damaged"))
    if current == BLD_MANOR and not damaged:
        return None
    if damaged:
        if current == building:
            return repair_cost(current, level)
        return None
    if current and current != building:
        return None
    if not current:
        return scaled_building_cost(building_upgrade_cost(building, 1), cost_mult)
    if level >= 3:
        return None
    return scaled_building_cost(building_upgrade_cost(building, level + 1), cost_mult)


def cheapest_build_action_cost(
    building: str,
    tiles: list[dict],
    *,
    cost_mult: float = 1.0,
) -> int | None:
    """Минимальная доступная цена по типу здания среди клеток усадьбы."""
    costs = [
        c
        for t in tiles
        if (c := build_action_cost(building, t, cost_mult=cost_mult)) is not None
    ]
    return min(costs) if costs else None


def min_any_build_action_cost(tiles: list[dict], *, cost_mult: float = 1.0) -> int | None:
    """Минимальная цена любого доступного строительства/апгрейда/ремонта."""
    costs = [
        c
        for building in PLAYER_BUILDINGS
        if (c := cheapest_build_action_cost(building, tiles, cost_mult=cost_mult)) is not None
    ]
    return min(costs) if costs else None


def gather_amount(resource: str) -> int:
    try:
        return GATHER_AMOUNTS[resource]
    except KeyError as exc:
        raise ValueError(f"Нельзя собрать: {resource}") from exc


def map_target_tiles(player_count: int) -> int:
    raw = max(MAP_MIN_TILES, int(round(player_count * TILES_PER_PLAYER)))
    return min(MAP_MAX_TILES, raw)


def best_rectangle(n: int) -> tuple[int, int]:
    """Подбирает width×height ≈ n, width >= height, closest area >= n within max."""
    n = max(MAP_MIN_TILES, min(MAP_MAX_TILES, n))
    best = (n, 1)
    best_score = abs(n - n) + abs(n - 1)
    for h in range(1, int(n**0.5) + 3):
        w = (n + h - 1) // h
        area = w * h
        if area > MAP_MAX_TILES:
            continue
        score = abs(area - n) + abs(w - h)
        if area >= n and (score < best_score or (score == best_score and area < best[0] * best[1])):
            best = (w, h)
            best_score = score
    return best
