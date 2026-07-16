"""Обзор владений усадьбы: клетки, здания и их эффект."""
from __future__ import annotations

from app import balance as B
from app.domain.economy import Production, building_production, tile_passive
from app.domain.map_gen import coord_label

_LEVEL_ROMAN = {1: "I", 2: "II", 3: "III"}

_BUILDING_HELP_LINES = (
    f"Двор - +{B.MANOR_GRAIN} зерна, +{B.MANOR_GOODS} товаров, "
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


def _format_prod_parts(prod: Production) -> list[str]:
    parts: list[str] = []
    if prod.grain:
        parts.append(f"+{prod.grain:.0f} зерна")
    if prod.goods:
        parts.append(f"+{prod.goods:.0f} товаров")
    if prod.might:
        parts.append(f"+{prod.might:.0f} силы")
    if prod.defense:
        parts.append(f"+{prod.defense:.0f} защиты")
    return parts


def _barn_effect_line(level: int) -> str:
    cap = B.stash_cap(level)
    protect_pct = int(round(B.barn_protect_frac(level) * 100))
    collect_days = B.collect_cap_days(level)
    return (
        f"склад до {cap} · бережёт {protect_pct}% при набеге · "
        f"сбор {collect_days} дн."
    )


def tile_effect_text(tile: dict, *, hungry: bool = False) -> str:
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
        extra = _format_prod_parts(passive)
        if extra:
            return f"{barn_line} · {', '.join(extra)}/день"
        return barn_line

    built = building_production(building, level, tile_type)
    total = Production(
        grain=passive.grain + built.grain,
        goods=passive.goods + built.goods,
        might=passive.might + built.might,
        defense=passive.defense + built.defense,
    )
    if hungry:
        total = total.scale(B.HUNGER_PRODUCTION_MULT)

    parts = _format_prod_parts(total)
    if not parts:
        return "без дохода"

    line = ", ".join(parts) + "/день"
    if building == B.BLD_MANOR and built.might:
        line += " (сила двора - до потолка дружины)"
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
            lines.append(f"  {tile_effect_text(tile, hungry=hungry)}")
            lines.append("")

    lines.append("Справка по зданиям:")
    for help_line in _BUILDING_HELP_LINES:
        lines.append(f"• {help_line}")

    if daily is not None:
        lines.append("")
        lines.append(
            f"Итого в день: +{daily.grain:.0f} зерна, +{daily.goods:.0f} товаров, "
            f"+{daily.might:.0f} силы · защита {daily.defense:.0f}"
        )
        if B.FIEF_BASE_GOODS:
            lines.append(
                f"(в итоге уже +{B.FIEF_BASE_GOODS} товаров базы усадьбы)"
            )

    return "\n".join(lines).rstrip() + "\n"
