"""Слухи долины: сплетни рынка с шансом лжи (чистые функции, без БД)."""
from __future__ import annotations

from dataclasses import dataclass, field
from random import Random
from typing import Any, Sequence

from app import balance as B
from app.domain.events import (
    MINOR_EVENTS,
    SHIPPED_CATASTROPHE_KEYS,
    SHIPPED_MINOR_KEYS,
    event_name_ru,
)

FACT_WEALTH = "wealth"
FACT_MIGHT = "might"
FACT_BUILDING = "building"
FACT_PATROL = "patrol"
FACT_TYPES = (FACT_WEALTH, FACT_MIGHT, FACT_BUILDING, FACT_PATROL)

TRUTH_FULL = "full"
TRUTH_FUZZY = "fuzzy"
TRUTH_FALSE = "false"

RUMOR_SECTION_HEADER = "👂 Слухи рынка:"
RUMOR_FOREIGN_SECTION_HEADER = "🗺 Из других долин:"
RUMOR_EMPTY_PULL = (
    "👂 Слухи рынка. Сегодня площадь молчит.\n"
    "Новые строки появляются в утренней сводке группы."
)

_MIGHT_SOFT_LABELS = ("тонкая", "крепкая", "толпа")


@dataclass(frozen=True)
class FiefRumorSnapshot:
    fief_id: int
    name: str
    grain: int
    goods: int
    might: int
    buildings: tuple[tuple[str, int], ...] = ()
    patrol_active: bool = False
    realm_title: str = ""


@dataclass(frozen=True)
class UpcomingEventHint:
    """Что рынок может предвещать: ключ и вид (minor/catastrophe)."""

    kind: str
    key: str


@dataclass(frozen=True)
class DailyRumorBundle:
    """Местные слухи + сплетни с других долин континента."""

    local: list[str] = field(default_factory=list)
    foreign: list[str] = field(default_factory=list)

    def as_storage(self) -> dict[str, list[str]]:
        return {"local": list(self.local), "foreign": list(self.foreign)}


def rumor_local_max_lines(player_count: int) -> int:
    """Потолок местных строк (без учёта предвестий событий)."""
    n = max(0, int(player_count))
    if n <= 0:
        return 0
    extra = (n - 1) // max(1, int(B.RUMOR_PLAYERS_PER_EXTRA_LINE))
    return min(int(B.RUMOR_MAX_CAP), int(B.RUMOR_MAX_PER_DAY) + extra)


def rumor_foreign_max_lines(foreign_player_count: int) -> int:
    """Потолок чужих строк - тот же масштаб, что у местных."""
    n = max(0, int(foreign_player_count))
    if n <= 0:
        return 0
    extra = (n - 1) // max(1, int(B.RUMOR_PLAYERS_PER_EXTRA_LINE))
    return min(int(B.RUMOR_FOREIGN_MAX_CAP), int(B.RUMOR_MAX_PER_DAY) + extra)


def parse_stored_rumors(raw: Any) -> DailyRumorBundle:
    """Читает last_rumor_lines: dict {local, foreign} или старый list."""
    if isinstance(raw, dict):
        local = raw.get("local") or []
        foreign = raw.get("foreign") or []
        return DailyRumorBundle(
            local=[str(x) for x in local],
            foreign=[str(x) for x in foreign],
        )
    if isinstance(raw, list):
        return DailyRumorBundle(local=[str(x) for x in raw], foreign=[])
    return DailyRumorBundle()


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


def might_soft_label(might: int) -> str:
    """Короткая метка дружины для UI (не точное число)."""
    band = might_band(might)
    return _MIGHT_SOFT_LABELS[max(0, min(len(_MIGHT_SOFT_LABELS) - 1, band))]


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
    all_bld = (B.BLD_MANOR, B.BLD_FARM, B.BLD_WORKSHOP, B.BLD_WATCH, B.BLD_BARN)
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


def compose_foreign_rumor_text(
    snap: FiefRumorSnapshot,
    fact_type: str,
    truth: str,
    rng: Random,
) -> str:
    body = compose_rumor_text(snap, fact_type, truth, rng)
    title = (snap.realm_title or "").strip() or "чужая долина"
    return f"Из долины {title}: {body}"


def _eligible_fact_types(snap: FiefRumorSnapshot) -> list[str]:
    types = [FACT_WEALTH, FACT_MIGHT, FACT_PATROL]
    if snap.buildings:
        types.append(FACT_BUILDING)
    return types


def _event_pool(kind: str) -> list[str]:
    if kind == "catastrophe":
        return sorted(SHIPPED_CATASTROPHE_KEYS)
    return sorted(k for k in SHIPPED_MINOR_KEYS if k in MINOR_EVENTS)


def compose_event_rumor(
    hint: UpcomingEventHint,
    rng: Random,
    *,
    accuracy: float | None = None,
) -> str:
    """Слух о грядущем событии: accuracy шанс назвать верный ключ."""
    accuracy = B.RUMOR_EVENT_ACCURACY if accuracy is None else accuracy
    pool = _event_pool(hint.kind)
    if not pool:
        return "На рынке шепчут, будто долина чего-то ждёт."
    named = hint.key
    if rng.random() >= accuracy:
        others = [k for k in pool if k != hint.key]
        if others:
            named = rng.choice(others)
    title = event_name_ru(hint.kind, named)
    if hint.kind == "catastrophe":
        return f"Шепчут, будто близится беда: {title}."
    return f"Говорят, завтра долина встретит: {title}."


def roll_event_rumor_lines(
    hints: Sequence[UpcomingEventHint],
    rng: Random,
    *,
    line_chance: float | None = None,
) -> list[str]:
    """До 1 строки-предвестия из доступных подсказок."""
    rng = rng or Random()
    if not hints:
        return []
    line_chance = B.RUMOR_EVENT_LINE_CHANCE if line_chance is None else line_chance
    if rng.random() >= line_chance:
        return []
    hint = rng.choice(list(hints))
    return [compose_event_rumor(hint, rng)]


def _roll_fief_rumor_lines(
    fiefs: Sequence[FiefRumorSnapshot],
    rng: Random,
    *,
    max_lines: int,
    line_chance: float,
    foreign: bool,
) -> list[str]:
    if max_lines <= 0 or not fiefs:
        return []
    used: set[tuple[int, str]] = set()
    lines: list[str] = []
    for _ in range(max_lines):
        if rng.random() >= line_chance:
            continue
        pool = list(fiefs)
        rng.shuffle(pool)
        placed = False
        for snap in pool:
            fact_pool = [
                f for f in _eligible_fact_types(snap) if (snap.fief_id, f) not in used
            ]
            if not fact_pool:
                continue
            fact = rng.choice(fact_pool)
            truth = _pick_truth(rng)
            if foreign:
                text = compose_foreign_rumor_text(snap, fact, truth, rng)
            else:
                text = compose_rumor_text(snap, fact, truth, rng)
            used.add((snap.fief_id, fact))
            lines.append(text)
            placed = True
            break
        if not placed:
            break
    return lines


def roll_daily_rumors(
    fiefs: Sequence[FiefRumorSnapshot],
    rng: Random | None = None,
    *,
    max_lines: int | None = None,
    line_chance: float | None = None,
    event_hints: Sequence[UpcomingEventHint] | None = None,
) -> list[str]:
    """Ролл местных слухов дня (список строк). Пустой = секцию не добавляем."""
    rng = rng or Random()
    event_lines = roll_event_rumor_lines(event_hints or (), rng)
    if max_lines is None:
        max_lines = rumor_local_max_lines(len(fiefs))
    line_chance = B.RUMOR_LINE_CHANCE if line_chance is None else line_chance
    fief_slots = max(0, int(max_lines) - len(event_lines))
    fief_lines = _roll_fief_rumor_lines(
        fiefs,
        rng,
        max_lines=fief_slots,
        line_chance=line_chance,
        foreign=False,
    )
    return list(event_lines) + fief_lines


def roll_valley_day_rumors(
    local_fiefs: Sequence[FiefRumorSnapshot],
    foreign_fiefs: Sequence[FiefRumorSnapshot],
    rng: Random | None = None,
    *,
    event_hints: Sequence[UpcomingEventHint] | None = None,
) -> DailyRumorBundle:
    """Местные слухи и чужой блок - один масштаб от числа усадеб."""
    rng = rng or Random()
    local = roll_daily_rumors(
        local_fiefs,
        rng,
        max_lines=rumor_local_max_lines(len(local_fiefs)),
        event_hints=event_hints,
    )
    foreign = _roll_fief_rumor_lines(
        foreign_fiefs,
        rng,
        max_lines=rumor_foreign_max_lines(len(foreign_fiefs)),
        line_chance=float(B.RUMOR_FOREIGN_LINE_CHANCE),
        foreign=True,
    )
    return DailyRumorBundle(local=local, foreign=foreign)


def format_rumor_section(
    lines: Sequence[str],
    *,
    foreign_lines: Sequence[str] | None = None,
) -> str | None:
    parts: list[str] = []
    if lines:
        body = "\n".join(f"• {line}" for line in lines)
        parts.append(f"{RUMOR_SECTION_HEADER}\n{body}")
    if foreign_lines:
        body = "\n".join(f"• {line}" for line in foreign_lines)
        parts.append(f"{RUMOR_FOREIGN_SECTION_HEADER}\n{body}")
    if not parts:
        return None
    return "\n\n".join(parts)


def format_rumors_pull(
    lines: Sequence[str],
    *,
    foreign_lines: Sequence[str] | None = None,
) -> str:
    section = format_rumor_section(lines, foreign_lines=foreign_lines)
    if not section:
        return RUMOR_EMPTY_PULL
    return (
        f"{section}\n\n"
        "<i>Сплетни площади - кто во что верит.</i>"
    )
