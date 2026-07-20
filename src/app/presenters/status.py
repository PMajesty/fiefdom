"""Статус-карточка усадьбы: чистый рендер из снимка."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StatusSnapshot:
    fief_label: str
    day_number: int
    alerts: tuple[str, ...]
    actions: int
    actions_max: int
    tile_count: int
    tile_cap: int
    stash_line: str
    barn_line: str
    production_line: str
    land_upkeep: int
    militia_upkeep: int
    next_tick_line: str
    prep_lines: tuple[str, ...]
    notes: tuple[str, ...]


def render_status_card(snapshot: StatusSnapshot) -> str:
    lines = [
        f"🏡 <b>{snapshot.fief_label}</b> · день {snapshot.day_number}",
        "",
    ]
    if snapshot.alerts:
        lines.extend(snapshot.alerts)
        lines.append("")
    lines.extend(
        [
            (
                f"⚡ Действия: {snapshot.actions}/{snapshot.actions_max} · "
                f"Клетки: {snapshot.tile_count}/{snapshot.tile_cap}"
            ),
            snapshot.stash_line,
            snapshot.barn_line,
            "",
            snapshot.production_line,
            (
                f"Корм: земля {snapshot.land_upkeep}, "
                f"дружина {snapshot.militia_upkeep}"
            ),
            "",
        ]
    )
    lines.append(snapshot.next_tick_line)
    if snapshot.prep_lines:
        lines.append("")
        lines.extend(snapshot.prep_lines)
    if snapshot.notes:
        lines.append("· " + " · ".join(snapshot.notes))
    return "\n".join(lines)
