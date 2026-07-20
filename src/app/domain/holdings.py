"""Обзор владений усадьбы: клетки, здания и их эффект."""
from __future__ import annotations

from app import balance as B
from app.domain.production import Production, building_production, tile_passive

from app.domain.map_gen import coord_label
from app.domain.resources import (
    add_bags,
    format_prod_parts,
    format_totals_production_line,
    live_resource_keys,
)

_LEVEL_ROMAN = {1: "I", 2: "II", 3: "III"}

_BUILDING_HELP_LINES = (
    f"Двор - +{B.MANOR_GRAIN} зерна/день, +{B.MANOR_GOODS} товаров/день, "
    f"+{B.MANOR_MIGHT} силы/день (сила - пока дружина ниже "
    f"{B.MILITIA_FREE})",
    "Ферма - зерно; на поле урожай ×"
    f"{B.NATIVE_BONUS:g}",
    "Мастерская - товары; в лесу ×"
    f"{B.NATIVE_BONUS:g}",
    "Сторожка - защита и сила; на холмах ×"
    f"{B.NATIVE_BONUS:g}",
    "Амбар - склад, бережёт запасы при набеге, больше дней сбора",
)


def building_level_roman(level: int) -> str:
    return _LEVEL_ROMAN.get(int(level), str(level))


def manor_might_applied(nominal: float, current_might: int) -> float:
    """Сколько силы двора реально копится при текущей дружине."""
    free_room = max(0, B.MILITIA_FREE - max(0, int(current_might)))
    return min(max(0.0, float(nominal)), float(free_room))


def _barn_effect_line(level: int) -> str:
    cap = B.stash_cap(level)
    protect_pct = int(round(B.barn_protect_frac(level) * 100))
    collect_days = B.collect_cap_days(level)
    return (
        f"склад до {cap} · бережёт {protect_pct}% при набеге · "
        f"сбор {collect_days} дн."
    )


def _manor_might_note(nominal: float, applied: float) -> str:
    cap = B.MILITIA_FREE
    if applied <= 0:
        return f"сила двора не копится: дружина уже у потолка ({cap})"
    if applied + 1e-9 < nominal:
        return f"сила двора урезана до потолка дружины ({cap})"
    return f"сила двора копится, пока дружина ниже {cap}"


def tile_effect_text(
    tile: dict,
    *,
    hungry: bool = False,
    current_might: int | None = None,
) -> str:
    """Что клетка даёт сейчас (одна строка без префикса)."""
    if tile.get("is_overgrown"):
        return "не даёт дохода (заросло)"

    building = tile.get("building") or ""
    level = int(tile.get("building_level") or 0)
    tile_type = tile.get("tile_type") or ""

    passive = tile_passive(tile_type)
    if building == B.BLD_BARN and level > 0:
        barn_line = _barn_effect_line(level)
        if hungry:
            passive = passive.scale(B.HUNGER_PRODUCTION_MULT)
        extra = format_prod_parts(passive.resources(), defense=passive.defense)
        if extra:
            return f"{barn_line} · {', '.join(extra)}"
        return barn_line

    built = building_production(building, level, tile_type)
    manor_note: str | None = None
    total_bag = add_bags(passive.resources(), built.resources())
    built_might = float(built.resources().get(B.RES_MIGHT, 0) or 0)
    if building == B.BLD_MANOR and built_might and current_might is not None:
        applied = manor_might_applied(built_might, current_might)
        manor_note = _manor_might_note(built_might, applied)
        total_bag[B.RES_MIGHT] = (
            float(passive.resources().get(B.RES_MIGHT, 0) or 0) + applied
        )

    total = Production.from_resources(
        total_bag, defense=passive.defense + built.defense
    )
    if hungry:
        total = total.scale(B.HUNGER_PRODUCTION_MULT)

    parts = format_prod_parts(total.resources(), defense=total.defense)
    if (
        building == B.BLD_MANOR
        and manor_note
        and total.resources().get(B.RES_MIGHT, 0) <= 0
    ):
        # Сила отключена потолком - в цифрах её нет, пояснение отдельно.
        other_bag = {
            key: total.resources()[key]
            for key in live_resource_keys()
            if key != B.RES_MIGHT
        }
        other = format_prod_parts(other_bag, defense=total.defense)
        if other:
            return f"{', '.join(other)} · {manor_note}"
        return manor_note

    if not parts:
        return "без дохода"

    line = ", ".join(parts)
    if manor_note:
        line += f" ({manor_note})"
    elif building == B.BLD_MANOR and built_might and current_might is None:
        line += f" (сила двора - пока дружина ниже {B.MILITIA_FREE})"
    return line


def tile_headline(tile: dict) -> str:
    coord = coord_label(int(tile["x"]), int(tile["y"]))
    terrain = B.TILE_NAMES_RU.get(tile.get("tile_type"), tile.get("tile_type") or "?")
    building = tile.get("building")
    level = int(tile.get("building_level") or 0)
    if building and level > 0:
        name = B.BUILDING_NAMES_RU.get(building, building)
        built = f"{name} {building_level_roman(level)}"
    else:
        built = "пусто"

    flags: list[str] = []
    if tile.get("is_overgrown"):
        flags.append("заросло")
    if tile.get("damaged") and building:
        flags.append("повреждено")
    flag_s = (" · " + ", ".join(flags)) if flags else ""
    return f"{coord} {terrain} · {built}{flag_s}"


def format_holdings(
    tiles: list[dict],
    *,
    fief_label: str,
    hungry: bool = False,
    daily: Production | None = None,
    current_might: int | None = None,
) -> str:
    """HTML-карточка владений для лички."""
    ordered = sorted(tiles, key=lambda t: (int(t["y"]), int(t["x"])))
    lines = [
        "🏞 <b>Владения</b>",
        f"{fief_label} · {len(ordered)}/{B.TILE_HARD_CAP} клеток",
    ]
    if hungry:
        lines.append("Голод: урожай с земли снижен вдвое.")
    lines.append("")

    if not ordered:
        lines.append("Клеток пока нет.")
    else:
        for tile in ordered:
            lines.append(tile_headline(tile))
            lines.append(
                f"  {tile_effect_text(tile, hungry=hungry, current_might=current_might)}"
            )
            lines.append("")

    lines.append("Справка по зданиям:")
    for help_line in _BUILDING_HELP_LINES:
        lines.append(f"• {help_line}")

    if daily is not None:
        lines.append("")
        lines.append(
            format_totals_production_line(daily.resources(), defense=daily.defense)
        )

    return "\n".join(lines).rstrip() + "\n"
