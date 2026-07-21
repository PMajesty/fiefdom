"""Слухи долины: редкие правдивые сплетни между тиками (чистые функции, без БД)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from typing import Any, Sequence

from app import balance as B
from app.domain.events import event_name_ru

FACT_WEALTH = "wealth"
FACT_MIGHT = "might"
FACT_BUILDING = "building"
FACT_PATROL = "patrol"
FACT_TYPES = (FACT_WEALTH, FACT_MIGHT, FACT_BUILDING, FACT_PATROL)

RUMOR_EMPTY_PULL = (
    "👂 Слухи рынка. Площадь пока молчит.\n"
    "Новые сплетни днём сами дойдут в личку."
)

_MIGHT_SOFT_LABELS = ("тонкая", "крепкая", "толпа")

_WEALTH_PHRASES = (
    "тощая кладовая",
    "запасы средние",
    "закрома тугие",
    "амбар ломится",
)
_MIGHT_PHRASES = (
    "дружина тонка",
    "дружина крепкая",
    "копий во дворе много",
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
    realm_title: str = ""


@dataclass(frozen=True)
class UpcomingEventHint:
    """Что рынок может предвещать: ключ и вид (minor/catastrophe)."""

    kind: str
    key: str


def rumor_subject_name(label: str) -> str:
    """Имя в слухах без Telegram-тега: убирает все `@` из подписи."""
    return str(label).replace("@", "")


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


def clamp_rumor_dues(
    dues: Sequence[datetime],
    *,
    max_count: int = 1,
) -> list[datetime]:
    """Не больше max_count due (самые ранние). Страховка от старых многоволновых очередей."""
    if max_count <= 0:
        return []
    return sorted(dues)[: int(max_count)]


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


def _wealth_phrase(band: int) -> str:
    band = max(0, min(len(_WEALTH_PHRASES) - 1, band))
    return _WEALTH_PHRASES[band]


def _might_phrase(band: int) -> str:
    band = max(0, min(len(_MIGHT_PHRASES) - 1, band))
    return _MIGHT_PHRASES[band]


def _roman(level: int) -> str:
    return {1: "I", 2: "II", 3: "III"}.get(level, str(level))


def _building_phrase(building: str, level: int) -> str:
    name = B.BUILDING_NAMES_RU.get(building, building)
    return f"стоит {name} {_roman(level)}"


def _patrol_phrase(active: bool) -> str:
    if active:
        return "дозор ходит у ворот"
    return "ворота без дозора"


def _wrap_intel_line(name: str, phrase: str) -> str:
    return f"У {name}, говорят, {phrase}."


def compose_rumor_text(
    snap: FiefRumorSnapshot,
    fact_type: str,
    rng: Random,
) -> str:
    """Одна правдивая строка про усадьбу."""
    name = snap.name

    if fact_type == FACT_WEALTH:
        band = wealth_band(wealth_total(snap.grain, snap.goods))
        return _wrap_intel_line(name, _wealth_phrase(band))

    if fact_type == FACT_MIGHT:
        band = might_band(snap.might)
        return _wrap_intel_line(name, _might_phrase(band))

    if fact_type == FACT_BUILDING:
        if not snap.buildings:
            return f"Про {name} на рынке шепчут - и тут же забывают."
        bld, level = rng.choice(snap.buildings)
        return _wrap_intel_line(name, _building_phrase(bld, level))

    if fact_type == FACT_PATROL:
        return _wrap_intel_line(name, _patrol_phrase(snap.patrol_active))

    return f"Про {name} на рынке шепчут - и тут же забывают."


def compose_foreign_rumor_text(
    snap: FiefRumorSnapshot,
    fact_type: str,
    rng: Random,
) -> str:
    body = compose_rumor_text(snap, fact_type, rng)
    title = (snap.realm_title or "").strip() or "чужая долина"
    return f"Из долины {title}: {body}"


def _eligible_fact_types(snap: FiefRumorSnapshot) -> list[str]:
    types = [FACT_WEALTH, FACT_MIGHT, FACT_PATROL]
    if snap.buildings:
        types.append(FACT_BUILDING)
    return types


def compose_event_rumor(hint: UpcomingEventHint) -> str:
    """Правдивое предвестие грядущего события."""
    title = event_name_ru(hint.kind, hint.key)
    if not title:
        return "На рынке шепчут, будто долина чего-то ждёт - сами не знают чего."
    if hint.kind == "catastrophe":
        return f"Шепчут, будто близится беда - {title}."
    return f"Говорят, на подходе - {title}."


def _compose_intel_line(
    snap: FiefRumorSnapshot,
    rng: Random,
    *,
    foreign: bool,
) -> str:
    facts = _eligible_fact_types(snap)
    fact = rng.choice(facts)
    if foreign:
        return compose_foreign_rumor_text(snap, fact, rng)
    return compose_rumor_text(snap, fact, rng)


def roll_rumor_line(
    local_snaps: Sequence[FiefRumorSnapshot],
    foreign_snaps: Sequence[FiefRumorSnapshot],
    event_hints: Sequence[UpcomingEventHint],
    rng: Random | None = None,
) -> str | None:
    """Одна строка: intel по дворам или (редко) правдивое предвестие события."""
    rng = rng or Random()
    local = list(local_snaps)
    foreign = list(foreign_snaps)

    hints = list(event_hints or ())
    if hints and rng.random() < float(B.RUMOR_EVENT_LINE_CHANCE):
        return compose_event_rumor(rng.choice(hints))

    pool: list[tuple[FiefRumorSnapshot, bool]] = (
        [(s, False) for s in local] + [(s, True) for s in foreign]
    )
    if not pool:
        return None
    snap, foreign_flag = rng.choice(pool)
    return _compose_intel_line(snap, rng, foreign=foreign_flag)


def _weighted_count(weights: Sequence[float], rng: Random, *, base: int) -> int:
    """Веса по возрастающему счёту: weights[i] -> base + i."""
    if not weights:
        return base
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 0:
        return base
    roll = rng.random() * total
    acc = 0.0
    for i, weight in enumerate(weights):
        acc += max(0.0, float(weight))
        if roll < acc:
            return base + i
    return base + len(weights) - 1


def format_rumor_wave(lines: Sequence[str]) -> str:
    """Текст поста: одна строка волны (пустой список → пустая строка)."""
    for line in lines:
        text = str(line).strip()
        if text:
            return text
    return ""


def roll_rumor_wave(
    local_snaps: Sequence[FiefRumorSnapshot],
    foreign_snaps: Sequence[FiefRumorSnapshot],
    event_hints: Sequence[UpcomingEventHint],
    rng: Random | None = None,
) -> list[str]:
    """0 или 1 строка на срабатывание due."""
    rng = rng or Random()
    line = roll_rumor_line(local_snaps, foreign_snaps, event_hints, rng)
    return [line] if line else []


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
    """Число волн на окно play: 0 или 1 по RUMOR_WINDOW_COUNT_WEIGHTS."""
    rng = rng or Random()
    weights = tuple(B.RUMOR_WINDOW_COUNT_WEIGHTS)
    return _weighted_count(weights, rng, base=0)


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
    """Случайный due внутри дневных щелей окна. Не больше одной волны."""
    rng = rng or Random()
    n = 1 if int(count) >= 1 else 0
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

    target = rng.random() * total_sec
    acc = 0.0
    due = segments[-1][0]
    for a, b in segments:
        span = (b - a).total_seconds()
        if acc + span >= target:
            due = a + timedelta(seconds=target - acc)
            break
        acc += span

    if in_rumor_quiet_hours(due, quiet_start=quiet_start, quiet_end=quiet_end):
        return []
    if not (window_start <= due < window_end):
        return []
    return [due]


def format_rumors_pull(lines: Sequence[str]) -> str:
    """DM catch-up: недавний архив, не свежий ролл."""
    clean = [str(x) for x in lines if str(x).strip()]
    if not clean:
        return RUMOR_EMPTY_PULL
    body = "\n".join(f"• {line}" for line in clean)
    return (
        f"👂 Недавний шёпот площади:\n{body}\n\n"
        "<i>Площадь не врёт. Новые слухи капают днём в личку.</i>"
    )
