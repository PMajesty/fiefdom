"""Слухи долины: сплетни рынка с шансом лжи (чистые функции, без БД)."""
from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Sequence

from app import balance as B

FACT_WEALTH = "wealth"
FACT_MIGHT = "might"
FACT_BUILDING = "building"
FACT_PATROL = "patrol"
FACT_TYPES = (FACT_WEALTH, FACT_MIGHT, FACT_BUILDING, FACT_PATROL)

TRUTH_FULL = "full"
TRUTH_FUZZY = "fuzzy"
TRUTH_FALSE = "false"

# Заголовок всегда напоминает новичкам: это не разведка.
RUMOR_SECTION_HEADER = "👂 Слухи (не факты - базар может врать):"
RUMOR_EMPTY_PULL = (
    "👂 Слухи - сплетни рынка, не разведка. Сегодня базар молчит.\n"
    "Новые строки появляются в утренней сводке группы."
)


@dataclass(frozen=True)
class FiefRumorSnapshot:
    fief_id: int
    name: str
    grain: int
    goods: int
    might: int
    buildings: tuple[tuple[str, int], ...] = ()
    patrol_active: bool = False


def wealth_total(grain: int, goods: int) -> int:
    return max(0, int(grain)) + max(0, int(goods))


def wealth_band(total: int) -> int:
    """0 тощая … 3 ломится."""
    thresholds = B.RUMOR_WEALTH_BANDS
    for i, edge in enumerate(thresholds):
        if total < edge:
            return i
    return len(thresholds)


def might_band(might: int) -> int:
    """0 тонка … 2 много копий."""
    thresholds = B.RUMOR_MIGHT_BANDS
    for i, edge in enumerate(thresholds):
        if might < edge:
            return i
    return len(thresholds)


def _shift_band(band: int, max_band: int, rng: Random, *, force_change: bool) -> int:
    if max_band <= 0:
        return 0
    if not force_change:
        return max(0, min(max_band, band))
    options = [i for i in range(max_band + 1) if i != band]
    if not options:
        return band
    return rng.choice(options)


def _wealth_phrase(band: int, *, fuzzy: bool) -> str:
    phrases = (
        ("тощая кладовая", "вроде пустовато в закромах"),
        ("сытая усадьба", "вроде запасы средние"),
        ("тугие закрома", "шепчут, что житница не худая"),
        ("амбар ломится", "базар орёт, что добро через край"),
    )
    band = max(0, min(len(phrases) - 1, band))
    clear, soft = phrases[band]
    return soft if fuzzy else clear


def _might_phrase(band: int, *, fuzzy: bool) -> str:
    phrases = (
        ("дружина тонка", "силы мало, если верить шепотку"),
        ("дружина крепкая", "вроде не слабая дружина"),
        ("много копий во дворе", "шепчут про толпу копий"),
    )
    band = max(0, min(len(phrases) - 1, band))
    clear, soft = phrases[band]
    return soft if fuzzy else clear


def _roman(level: int) -> str:
    return {1: "I", 2: "II", 3: "III"}.get(level, str(level))


def _building_phrase(
    building: str,
    level: int,
    *,
    fuzzy: bool,
) -> str:
    name = B.BUILDING_NAMES_RU.get(building, building)
    if fuzzy:
        return f"вроде стоит {name.lower()}"
    return f"стоит {name} {_roman(level)}"


def _patrol_phrase(active: bool, *, fuzzy: bool) -> str:
    if active:
        if fuzzy:
            return "вроде по ночам ходят с факелами"
        return "дозор ходит у ворот"
    if fuzzy:
        return "ворота вроде без лишнего шуму"
    return "ворота без дозора"


def _pick_truth(rng: Random) -> str:
    roll = rng.random()
    if roll < B.RUMOR_TRUTH_FULL:
        return TRUTH_FULL
    if roll < B.RUMOR_TRUTH_FULL + B.RUMOR_TRUTH_FUZZY:
        return TRUTH_FUZZY
    return TRUTH_FALSE


def _false_building(
    snap: FiefRumorSnapshot,
    rng: Random,
) -> tuple[str, int]:
    all_bld = (B.BLD_FARM, B.BLD_WORKSHOP, B.BLD_WATCH, B.BLD_BARN)
    owned = {b for b, _ in snap.buildings}
    missing = [b for b in all_bld if b not in owned]
    if missing:
        return rng.choice(missing), rng.randint(1, 3)
    if not snap.buildings:
        return rng.choice(all_bld), rng.randint(1, 2)
    bld, level = rng.choice(snap.buildings)
    wrong = max(1, min(3, level + rng.choice([-1, 1])))
    if wrong == level:
        wrong = 1 if level != 1 else 2
    return bld, wrong


def compose_rumor_text(
    snap: FiefRumorSnapshot,
    fact_type: str,
    truth: str,
    rng: Random,
) -> str:
    name = snap.name
    fuzzy = truth == TRUTH_FUZZY
    false = truth == TRUTH_FALSE

    if fact_type == FACT_WEALTH:
        true_band = wealth_band(wealth_total(snap.grain, snap.goods))
        band = _shift_band(true_band, 3, rng, force_change=false)
        return f"У {name}, говорят, {_wealth_phrase(band, fuzzy=fuzzy)}."

    if fact_type == FACT_MIGHT:
        true_band = might_band(snap.might)
        band = _shift_band(true_band, 2, rng, force_change=false)
        return f"У {name}, говорят, {_might_phrase(band, fuzzy=fuzzy)}."

    if fact_type == FACT_BUILDING:
        if false or not snap.buildings:
            bld, level = _false_building(snap, rng)
        else:
            bld, level = rng.choice(snap.buildings)
        return f"У {name}, говорят, {_building_phrase(bld, level, fuzzy=fuzzy)}."

    if fact_type == FACT_PATROL:
        active = (not snap.patrol_active) if false else snap.patrol_active
        return f"У {name}, говорят, {_patrol_phrase(active, fuzzy=fuzzy)}."

    return f"У {name} что-то шепчут на рынке - и тут же забывают."


def _eligible_fact_types(snap: FiefRumorSnapshot) -> list[str]:
    types = [FACT_WEALTH, FACT_MIGHT, FACT_PATROL]
    if snap.buildings:
        types.append(FACT_BUILDING)
    return types


def roll_daily_rumors(
    fiefs: Sequence[FiefRumorSnapshot],
    rng: Random | None = None,
    *,
    max_lines: int | None = None,
    line_chance: float | None = None,
) -> list[str]:
    """Ролл слухов дня. Пустой список = в сводку секцию не добавляем."""
    rng = rng or Random()
    if len(fiefs) < 1:
        return []

    max_lines = B.RUMOR_MAX_PER_DAY if max_lines is None else max_lines
    line_chance = B.RUMOR_LINE_CHANCE if line_chance is None else line_chance
    used: set[tuple[int, str]] = set()
    lines: list[str] = []

    for _ in range(max_lines):
        if rng.random() >= line_chance:
            continue
        pool = list(fiefs)
        rng.shuffle(pool)
        placed = False
        for snap in pool:
            fact_pool = [f for f in _eligible_fact_types(snap) if (snap.fief_id, f) not in used]
            if not fact_pool:
                continue
            fact = rng.choice(fact_pool)
            truth = _pick_truth(rng)
            text = compose_rumor_text(snap, fact, truth, rng)
            used.add((snap.fief_id, fact))
            lines.append(text)
            placed = True
            break
        if not placed:
            break

    return lines


def format_rumor_section(lines: Sequence[str]) -> str | None:
    if not lines:
        return None
    body = "\n".join(f"• {line}" for line in lines)
    return f"{RUMOR_SECTION_HEADER}\n{body}"


def format_rumors_pull(lines: Sequence[str]) -> str:
    section = format_rumor_section(lines)
    if not section:
        return RUMOR_EMPTY_PULL
    return (
        f"{section}\n\n"
        "<i>Это сплетни, не доклад разведки. Верить базарату - на свой страх.</i>"
    )
