"""Реестр патч-анонсов: RP-вестники о правилах, вступающих в силу mid-game."""
from __future__ import annotations

from dataclasses import dataclass

from app import balance as B


@dataclass(frozen=True)
class PatchNote:
    """Один разовый вестник. id стабилен и совпадает с ключом в announced_patches."""

    id: str
    title: str
    body_lines: tuple[str, ...]


def format_patch_announcement(note: PatchNote) -> str:
    """HTML-текст для группового чата долины."""
    bullets = "\n".join(f"• {line}" for line in note.body_lines)
    return (
        f"📯 <b>Вестник долины</b>\n"
        f"<b>{note.title}</b>\n\n"
        f"{bullets}"
    )


def pending_patch_notes(announced_ids: set[str]) -> list[PatchNote]:
    return [note for note in PATCH_NOTES if note.id not in announced_ids]


# Только новые патчи после появления механики - без backfill старой истории.
PATCH_NOTES: tuple[PatchNote, ...] = (
    PatchNote(
        id="raid_victim_shield_one_tick_v1",
        title="Глашатай стучит посохом: щит после набега стал короче",
        body_lines=(
            (
                f"После удачного удара щит жертвы держится {B.RAID_VICTIM_SHIELD_TICKS} тик - "
                "до следующего хода долины, а не трое суток мелких часов."
            ),
            (
                "Щит общий: пока он стоит, на усадьбу не ходит никто, "
                "и сама она копья не поднимает."
            ),
            "Жадные, считайте быстрее. Долгих укрытий долина больше не терпит.",
        ),
    ),
)
