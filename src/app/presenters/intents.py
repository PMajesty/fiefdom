"""Карточки и строки подготовленных заявок: чистый рендер."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreparedRaidView:
    target_label: str
    might: int
    is_open: bool


@dataclass(frozen=True)
class PreparedCaravanView:
    target_label: str
    amount: int
    resource_name: str


def render_prepared_intent_status_lines(
    raids: tuple[PreparedRaidView, ...],
    caravans: tuple[PreparedCaravanView, ...],
) -> list[str]:
    if not raids and not caravans:
        return []
    lines = ["Заявки:"]
    for raid in raids:
        st = "открыта" if raid.is_open else "закрыта"
        lines.append(
            f"· набег на {raid.target_label}: {raid.might} силы ({st})"
        )
    for caravan in caravans:
        lines.append(
            f"· обоз к {caravan.target_label}: "
            f"{caravan.amount} {caravan.resource_name} (в пути)"
        )
    return lines


def render_prepared_intents_card(
    raids: tuple[PreparedRaidView, ...],
    caravans: tuple[PreparedCaravanView, ...],
) -> str:
    if not raids and not caravans:
        return (
            "Нет подготовленных заявок: ни набега, ни обоза в пути.\n"
            "Объявить набег - в Усадьбе, обоз - в Долине."
        )
    lines = [
        "<b>Заявки</b>",
        "Открытые можно снять кнопками ниже; закрытый набег уже не отменить.",
        "",
    ]
    if raids:
        lines.append("Набеги:")
        for raid in raids:
            st = "открыта" if raid.is_open else "закрыта"
            lines.append(
                f"· на {raid.target_label}: {raid.might} силы ({st})"
            )
        lines.append("")
    if caravans:
        lines.append("Обозы:")
        for caravan in caravans:
            lines.append(
                f"· к {caravan.target_label}: "
                f"{caravan.amount} {caravan.resource_name} (можно вернуть)"
            )
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
