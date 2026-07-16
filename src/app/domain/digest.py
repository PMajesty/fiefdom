"""Форматирование утренней сводки и указов для группового чата."""
from __future__ import annotations


def format_digest(
    *,
    realm_title: str,
    day: int,
    night_lines: list[str],
    event_line: str | None,
    market_line: str | None,
    feud_lines: list[str],
    sunday_extra: str | None,
) -> str:
    night = " ".join(night_lines) if night_lines else "тихо."
    parts = [
        f"🏰 {realm_title} — день {day}",
        f"🌙 Ночью: {night}",
    ]
    if event_line:
        parts.append(f"📜 Сегодня: {event_line}")
    if market_line:
        parts.append(f"🛒 Рынок: {market_line}")
    for feud in feud_lines:
        parts.append(f"⚔️ Вражда: {feud}")
    if sunday_extra:
        parts.append(sunday_extra)
    return "\n".join(parts)


def format_decree(number: int, text: str) -> str:
    return f"📜 УКАЗ №{number}\n{text}"
