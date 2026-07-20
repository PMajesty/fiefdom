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
    is_open: bool


@dataclass(frozen=True)
class PreparedCoverView:
    stance_label: str
    budget: int
    is_open: bool


def render_prepared_intent_status_lines(
    raids: tuple[PreparedRaidView, ...],
    caravans: tuple[PreparedCaravanView, ...],
    covers: tuple[PreparedCoverView, ...] = (),
) -> list[str]:
    if not raids and not caravans and not covers:
        return []
    lines = ["Заявки:"]
    for raid in raids:
        st = "открыта" if raid.is_open else "закрыта"
        lines.append(
            f"· набег на {raid.target_label}: {raid.might} силы ({st})"
        )
    for caravan in caravans:
        st = "открыт" if caravan.is_open else "закрыт"
        lines.append(
            f"· обоз к {caravan.target_label}: "
            f"{caravan.amount} {caravan.resource_name} ({st})"
        )
    for cover in covers:
        st = "открыта" if cover.is_open else "закрыта"
        lines.append(
            f"· застава ({cover.stance_label}): {cover.budget} силы ({st})"
        )
    return lines


def render_prepared_intents_card(
    raids: tuple[PreparedRaidView, ...],
    caravans: tuple[PreparedCaravanView, ...],
    covers: tuple[PreparedCoverView, ...] = (),
) -> str:
    if not raids and not caravans and not covers:
        return (
            "Нет подготовленных заявок: ни набега, ни обоза, ни заставы.\n"
            "Объявить набег - в Усадьбе, обоз - в Долине, заставу - в Пакте."
        )
    lines = [
        "<b>Заявки</b>",
        "Открытые можно снять кнопками ниже; закрытые уже не отменить.",
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
            st = "можно вернуть" if caravan.is_open else "закрыт"
            lines.append(
                f"· к {caravan.target_label}: "
                f"{caravan.amount} {caravan.resource_name} ({st})"
            )
        lines.append("")
    if covers:
        lines.append("Застава:")
        for cover in covers:
            st = "можно снять" if cover.is_open else "закрыта"
            lines.append(
                f"· {cover.stance_label}: {cover.budget} силы ({st})"
            )
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
