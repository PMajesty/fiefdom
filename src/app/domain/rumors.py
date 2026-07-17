"""Слухи долины: капельные сплетни между тиками (чистые функции, без БД)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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

RUMOR_EMPTY_PULL = (
    "👂 Слухи рынка. Пока площадь молчит.\n"
    "Новые строки выходят днём в групповом чате долины."
)

RUMOR_OPENERS = (
    "Говорят...",
    "Слыхали...",
    "На рынке шепчут...",
    "В пивной бормочут...",
    "Бабы у колодца судачат...",
)

# Пьяная болтовня площади: смешно и грубо, без админского яда.
_FLUFF_TEMPLATES = (
    "у {name} пиво кончилось раньше зарплаты дружины.",
    "у {name} копьё мягче редьки, а язык длиннее обоза.",
    "у {name} амбар гудит пустотой - мыши съезд созвали.",
    "у {name} пахнет так, что козы в лес просятся.",
    "у {name} лень встала раньше солнца и села на порог.",
    "у {name} жадность зерно считает во сне и просыпается злее.",
    "у {name} удача убежала через дыру в штанах.",
    "у {name} дружина храбрая только до первой кружки.",
    "у {name} в кабаке хвастают силой, а в поле - мозолями на языке.",
    "у {name} корова доит лучше хозяина, да стыдливее.",
    "у {name} ворота скрипят громче, чем хозяин работает.",
    "у {name} в закромах мышь богаче князя.",
    "про {name} говорят: нос в чужой амбар - руки в свой карман.",
    "у {name} копьё в бою дрожит, а кружка - никогда.",
    "у {name} двор широкий, а дела узкие, как щель в заборе.",
    "у {name} хвалят щедрость - пока не спросят взаймы.",
    "у {name} петух будит соседей, а хозяин спит до обеда.",
    "у {name} конь умнее седока - и стыдливее про это молчит.",
    "у {name} шлем блестит, а колени стучат громче барабана.",
    "у {name} долги растут быстрее репы после дождя.",
    "у {name} хлеб черствее хозяйской совести.",
    "у {name} собака лает на воров, хозяин - на работу.",
    "про {name} говорят: в рейде лев, в поле - табуретка.",
    "у {name} стрела летит гордо, пока не спросишь, куда целил.",
    "у {name} сапоги крепче духа: дух протёрся на крыльце.",
    "у {name} ложка в каше храбрее меча в ножнах.",
    "у {name} гусь жирнее казны, да честнее считает зерно.",
    "у {name} мельница крутится, а мука всё чужая.",
    "у {name} кот ловит мышей лучше, чем хозяин - удачу.",
    "у {name} тень храбрая, пока солнце за спиной.",
    "у {name} рука на клятве тёплая, на кошельке - ледяная.",
    "у {name} сон крепче дозора: дозор хоть глаза трёт.",
    "у {name} дым из трубы густой, а обед из него не сварить.",
    "у {name} ведро в колодце звенит пустотой громче колокола.",
    "у {name} крыша течёт ровнее, чем правда с языка.",
    "у {name} свинья в хлеву сытее гостя за столом.",
    "у {name} соль на столе дороже обещаний в кабаке.",
    "у {name} свеча догорает быстрее терпения дружины.",
    "у {name} мост через лужу шаткий, а через совесть - вовсе дыра.",
    "у {name} мешок с зерном худеет быстрее хозяйской улыбки.",
    "у {name} зеркало врёт мягче соседей у колодца.",
    "у {name} зимой храбрость мёрзнет первой, кружка - последней.",
    "у {name} топор тупой, зато язык наточен до заусенцев.",
    "у {name} курица несёт яйца, хозяин - отговорки.",
    "у {name} забор высокий от воров, низкий от собственной лени.",
    "про {name} говорят: в пиру орёл, на пашне - воробей без крыльев.",
    "у {name} щит дырявый, зато хвастовство без единой щели.",
    "у {name} бочка в погребе гулкая - эхо богаче содержимого.",
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


def parse_stored_rumors(raw: Any) -> list[str]:
    """Читает архив last_rumor_lines: плоский list или legacy {local, foreign}."""
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, dict):
        local = raw.get("local") or []
        foreign = raw.get("foreign") or []
        return [str(x) for x in list(local) + list(foreign) if str(x).strip()]
    return []


def parse_rumor_queue(raw: Any) -> list[datetime]:
    """Очередь due-времён: list ISO-строк или datetime."""
    if not isinstance(raw, list):
        return []
    out: list[datetime] = []
    for item in raw:
        if isinstance(item, datetime):
            out.append(item)
            continue
        if isinstance(item, dict):
            item = item.get("due")
        if not item:
            continue
        try:
            out.append(datetime.fromisoformat(str(item)))
        except ValueError:
            continue
    return out


def rumor_queue_storage(dues: Sequence[datetime]) -> list[str]:
    return [d.isoformat() for d in dues]


def append_rumor_archive(
    archive: Sequence[str],
    line: str,
    *,
    max_lines: int | None = None,
) -> list[str]:
    cap = int(B.RUMOR_ARCHIVE_MAX if max_lines is None else max_lines)
    lines = [str(x) for x in archive if str(x).strip()]
    text = str(line).strip()
    if text:
        lines.append(text)
    if cap > 0:
        lines = lines[-cap:]
    return lines


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


def compose_fluff_rumor(name: str, rng: Random) -> str:
    """Именная кабацкая болтовня с открывалкой."""
    opener = rng.choice(RUMOR_OPENERS)
    body = rng.choice(_FLUFF_TEMPLATES).format(name=name)
    return f"{opener} {body}"


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


def _compose_intel_line(
    snap: FiefRumorSnapshot,
    rng: Random,
    *,
    foreign: bool,
) -> str:
    facts = _eligible_fact_types(snap)
    fact = rng.choice(facts)
    truth = _pick_truth(rng)
    if foreign:
        return compose_foreign_rumor_text(snap, fact, truth, rng)
    return compose_rumor_text(snap, fact, truth, rng)


def roll_rumor_line(
    local_snaps: Sequence[FiefRumorSnapshot],
    foreign_snaps: Sequence[FiefRumorSnapshot],
    event_hints: Sequence[UpcomingEventHint],
    rng: Random | None = None,
) -> str | None:
    """Одна строка смешанного пула: 25% fluff, иначе intel (+ редко предвестие)."""
    rng = rng or Random()
    local = list(local_snaps)
    foreign = list(foreign_snaps)
    named = [s for s in local + foreign if (s.name or "").strip()]
    fluff_chance = float(B.RUMOR_FLUFF_CHANCE)
    if named and rng.random() < fluff_chance:
        return compose_fluff_rumor(rng.choice(named).name, rng)

    hints = list(event_hints or ())
    if hints and rng.random() < float(B.RUMOR_EVENT_LINE_CHANCE):
        return compose_event_rumor(rng.choice(hints), rng)

    pool: list[tuple[FiefRumorSnapshot, bool]] = (
        [(s, False) for s in local] + [(s, True) for s in foreign]
    )
    if not pool:
        if named:
            return compose_fluff_rumor(rng.choice(named).name, rng)
        return None
    snap, foreign_flag = rng.choice(pool)
    return _compose_intel_line(snap, rng, foreign=foreign_flag)


def in_rumor_quiet_hours(
    local_now: datetime,
    *,
    quiet_start: int | None = None,
    quiet_end: int | None = None,
) -> bool:
    """Тишина рынка: [quiet_start, 24) U [0, quiet_end)."""
    start = int(B.RUMOR_QUIET_START_HOUR if quiet_start is None else quiet_start)
    end = int(B.RUMOR_QUIET_END_HOUR if quiet_end is None else quiet_end)
    hour = int(local_now.hour)
    return hour >= start or hour < end


def rumor_count_for_window(rng: Random | None = None) -> int:
    """1 часто / 2 иногда. Никогда 3."""
    rng = rng or Random()
    weights = tuple(B.RUMOR_WINDOW_COUNT_WEIGHTS)
    one_w = float(weights[0]) if weights else 0.7
    return 1 if rng.random() < one_w else 2


def _segment_non_quiet(
    day_start: datetime,
    day_end: datetime,
    quiet_start: int,
    quiet_end: int,
) -> list[tuple[datetime, datetime]]:
    """Непересекающиеся куски одного календарного дня вне тихих часов."""
    if day_end <= day_start:
        return []
    day = day_start.date()
    tz = day_start.tzinfo
    quiet_from = datetime(day.year, day.month, day.day, quiet_start, 0, tzinfo=tz)
    quiet_to = datetime(day.year, day.month, day.day, quiet_end, 0, tzinfo=tz)
    # Тишина: [0, quiet_end) и [quiet_start, 24).
    allowed = [
        (
            datetime(day.year, day.month, day.day, quiet_end, 0, tzinfo=tz),
            quiet_from,
        )
    ]
    out: list[tuple[datetime, datetime]] = []
    for seg_a, seg_b in allowed:
        a = max(day_start, seg_a)
        b = min(day_end, seg_b)
        if b > a:
            out.append((a, b))
    return out


def allowed_rumor_segments(
    window_start: datetime,
    window_end: datetime,
    *,
    quiet_start: int | None = None,
    quiet_end: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """Допустимые отрезки окна. Через ночную тишину - только утренний хвост."""
    start_h = int(B.RUMOR_QUIET_START_HOUR if quiet_start is None else quiet_start)
    end_h = int(B.RUMOR_QUIET_END_HOUR if quiet_end is None else quiet_end)
    if window_end <= window_start:
        return []
    segments: list[tuple[datetime, datetime]] = []
    cursor = window_start
    while cursor.date() <= window_end.date():
        day = cursor.date()
        day_start = datetime(day.year, day.month, day.day, 0, 0, tzinfo=cursor.tzinfo)
        day_end = day_start + timedelta(days=1)
        slice_start = max(window_start, day_start)
        slice_end = min(window_end, day_end)
        segments.extend(_segment_non_quiet(slice_start, slice_end, start_h, end_h))
        cursor = day_end
        if cursor >= window_end:
            break
    if len(segments) >= 2:
        # Окно через ночь: не копим вечерний обрубок, только утро до тика.
        return [segments[-1]]
    return segments


def plan_rumor_due_times(
    window_start: datetime,
    window_end: datetime,
    count: int,
    *,
    quiet_start: int | None = None,
    quiet_end: int | None = None,
    rng: Random | None = None,
) -> list[datetime]:
    """Случайные due внутри дневных щелей окна. Без тихих часов."""
    rng = rng or Random()
    n = max(0, min(2, int(count)))
    if n <= 0:
        return []
    segments = allowed_rumor_segments(
        window_start,
        window_end,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
    )
    if not segments:
        return []
    total_sec = sum((b - a).total_seconds() for a, b in segments)
    if total_sec <= 0:
        return []

    def _pick() -> datetime:
        target = rng.random() * total_sec
        acc = 0.0
        for a, b in segments:
            span = (b - a).total_seconds()
            if acc + span >= target:
                return a + timedelta(seconds=target - acc)
            acc += span
        return segments[-1][0]

    dues = sorted(_pick() for _ in range(n))
    if n == 2 and dues[0] == dues[1] and total_sec >= 2:
        # Чуть разводим совпавшие броски.
        dues[1] = min(segments[-1][1] - timedelta(seconds=1), dues[0] + timedelta(minutes=1))
        if dues[1] <= dues[0]:
            dues = [dues[0]]
    # Страховка: ничего в тишине.
    dues = [
        d
        for d in dues
        if not in_rumor_quiet_hours(d, quiet_start=quiet_start, quiet_end=quiet_end)
        and window_start <= d < window_end
    ]
    return dues[:n]


def format_rumors_pull(lines: Sequence[str]) -> str:
    """DM catch-up: недавний архив, не свежий ролл."""
    clean = [str(x) for x in lines if str(x).strip()]
    if not clean:
        return RUMOR_EMPTY_PULL
    body = "\n".join(f"• {line}" for line in clean)
    return (
        f"👂 Недавний шёпот площади:\n{body}\n\n"
        "<i>Сплетни рынка - кто во что верит. Новые выходят днём в группе.</i>"
    )
