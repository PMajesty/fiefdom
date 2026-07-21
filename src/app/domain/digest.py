"""Форматирование утренней сводки и указов долины."""
from __future__ import annotations


def ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение для целого числа: 1 лот / 2 лота / 5 лотов."""
    n_abs = abs(int(n)) % 100
    if 11 <= n_abs <= 14:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def format_lots_count(n: int) -> str:
    n = int(n)
    return f"{n} {ru_plural(n, 'лот', 'лота', 'лотов')}"


def format_digest(
    *,
    realm_title: str,
    day: int,
    night_lines: list[str],
    event_line: str | None,
    feud_lines: list[str],
    sunday_extra: str | None,
) -> str:
    night = " ".join(night_lines) if night_lines else "тихо."
    parts = [
        f"🏰 {realm_title} - день {day}",
        f"🌙 Ночью: {night}",
    ]
    if event_line:
        parts.append(f"📜 Сегодня: {event_line}")
    for feud in feud_lines:
        parts.append(f"⚔️ Вражда: {feud}")
    if sunday_extra:
        parts.append(sunday_extra)
    return "\n\n".join(parts)


def format_decree(number: int, text: str) -> str:
    return f"📜 УКАЗ №{number}\n{text}"
