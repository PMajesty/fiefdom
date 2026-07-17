"""Диспетчер эффектов мелких событий и катастроф (без доступа к БД)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from random import Random
from typing import Any, Callable

from app import balance as B
from app.domain.events import CATASTROPHES, catastrophe_effect, minor_effect


@dataclass(frozen=True)
class InstantMinorCtx:
    """Контекст мгновенного минора: колбэки пишет Engine."""

    fiefs: list[dict[str, Any]]
    barn_level: Callable[[int], int]
    fief_tiles: Callable[[int], list[dict[str, Any]]]
    update_fief: Callable[..., None]
    update_tile: Callable[..., None]
    rng: Random


@dataclass(frozen=True)
class CatastropheResolveCtx:
    """Контекст resolve катастрофы: колбэки пишет scheduler/Engine."""

    event_id: int
    fiefs: list[dict[str, Any]]
    event_actions: list[dict[str, Any]]
    get_fief: Callable[[int], dict[str, Any] | None]
    update_fief: Callable[..., None]
    update_event: Callable[..., None]


def minor_farm_mult(key: str | None) -> float:
    """Множитель ферм от активного минора (1.0 если нет farm_mult)."""
    if not key:
        return 1.0
    try:
        eff = minor_effect(key)
    except KeyError:
        return 1.0
    if "farm_mult" not in eff:
        return 1.0
    return float(eff["farm_mult"])


def catastrophe_farm_mult(key: str | None) -> float:
    """Множитель ферм от активной катастрофы (1.0 если нет farm_mult)."""
    if not key:
        return 1.0
    try:
        eff = catastrophe_effect(key)
    except KeyError:
        return 1.0
    if "farm_mult" not in eff:
        return 1.0
    return float(eff["farm_mult"])


def realm_farm_mult(
    *,
    active_minor_key: str | None,
    active_catastrophe_keys: list[str] | tuple[str, ...] = (),
) -> float:
    """Составной множитель ферм: минор × все катастрофы с farm_mult."""
    mult = minor_farm_mult(active_minor_key)
    for key in active_catastrophe_keys:
        mult *= catastrophe_farm_mult(key)
    return mult


def minor_upgrade_cost_mult(key: str | None) -> float:
    if not key:
        return 1.0
    try:
        return float(minor_effect(key).get("upgrade_cost_mult", 1.0))
    except KeyError:
        return 1.0


def minor_trade_bonus_frac(key: str | None) -> float:
    if not key:
        return 0.0
    try:
        return float(minor_effect(key).get("trade_bonus_frac") or 0.0)
    except KeyError:
        return 0.0


def minor_fog_ignores_patrol(key: str | None) -> bool:
    if not key:
        return False
    try:
        return bool(minor_effect(key).get("raids_ignore_patrol"))
    except KeyError:
        return False


def minor_wedding_gift_grain(key: str | None) -> int:
    """Зерно \"на свадьбу\" при завершении обмена; 0 если событие не wedding."""
    if not key:
        return 0
    try:
        return int(minor_effect(key).get("trade_gift_grain") or 0)
    except KeyError:
        return 0


def _apply_rats(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    threshold = int(eff.get("unprot_grain_threshold") or 80)
    loss_frac = float(eff.get("loss_frac") or 0.25)
    for fief in ctx.fiefs:
        barn = ctx.barn_level(int(fief["id"]))
        unprot = int(fief["grain"] * (1.0 - B.barn_protect_frac(barn)))
        if unprot > threshold:
            loss = max(1, int(unprot * loss_frac))
            ctx.update_fief(fief["id"], grain=max(0, fief["grain"] - loss))


def _apply_blight(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    frac = float(eff.get("goods_loss_frac") or 0.225)
    for fief in ctx.fiefs:
        loss = max(1, int(int(fief["goods"]) * frac)) if int(fief["goods"]) > 0 else 0
        if loss:
            ctx.update_fief(fief["id"], goods=max(0, int(fief["goods"]) - loss))


def _apply_spoilage(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    frac = float(eff.get("grain_loss_frac") or 0.1875)
    for fief in ctx.fiefs:
        loss = max(1, int(int(fief["grain"]) * frac)) if int(fief["grain"]) > 0 else 0
        if loss:
            ctx.update_fief(fief["id"], grain=max(0, int(fief["grain"]) - loss))


def _apply_toll(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    flat = int(eff.get("goods_flat_loss") or 15)
    for fief in ctx.fiefs:
        ctx.update_fief(fief["id"], goods=max(0, int(fief["goods"]) - flat))


def _apply_press_gang(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    loss = int(eff.get("might_loss") or 4)
    for fief in ctx.fiefs:
        ctx.update_fief(fief["id"], might=max(0, int(fief["might"]) - loss))


def _apply_fire(eff: dict[str, Any], ctx: InstantMinorCtx) -> None:
    del eff  # флаг damage_random_building; логика ниже
    for fief in ctx.fiefs:
        tiles = [
            t
            for t in ctx.fief_tiles(int(fief["id"]))
            if t.get("building")
            and t.get("building") != B.BLD_MANOR
            and not t.get("is_overgrown")
            and not t.get("damaged")
        ]
        if not tiles:
            continue
        victim = ctx.rng.choice(tiles)
        ctx.update_tile(victim["id"], damaged=True)


_INSTANT_MINOR_HANDLERS: dict[str, Callable[[dict[str, Any], InstantMinorCtx], None]] = {
    "rats": _apply_rats,
    "blight": _apply_blight,
    "spoilage": _apply_spoilage,
    "toll": _apply_toll,
    "press_gang": _apply_press_gang,
    "fire": _apply_fire,
}


def apply_instant_minor(key: str, ctx: InstantMinorCtx) -> None:
    """Применяет мгновенный эффект отгруженного минора; no-op для флагов/omen."""
    eff = minor_effect(key)
    handler = _INSTANT_MINOR_HANDLERS.get(key)
    if handler is None:
        return
    handler(eff, ctx)


def _resolve_bandit_night(eff: dict[str, Any], ctx: CatastropheResolveCtx) -> str:
    fiefs = ctx.fiefs
    players = max(1, len(fiefs))
    threshold = int(math.ceil(float(eff.get("might_per_player") or 0) * players))
    actions = ctx.event_actions
    total_might = sum(int(a.get("amount") or 0) for a in actions)
    contributors = {int(a["fief_id"]) for a in actions if int(a.get("amount") or 0) > 0}

    if total_might >= threshold:
        ctx.update_event(ctx.event_id, status="resolved")
        loot_each = int(eff.get("loot_goods_per_player") or 0)
        if contributors:
            share = max(1, int((loot_each * players) // len(contributors)))
            for fid in contributors:
                f = ctx.get_fief(fid)
                if f:
                    ctx.update_fief(fid, goods=f["goods"] + share)
        return (
            f"⚔️ Ночь бандитов отбита! Собрано {total_might}/{threshold} силы. "
            f"Участники получили добычу."
        )

    loss_frac = float(eff.get("fail_unprot_grain_frac") or 0)
    loss_note = []
    for f in fiefs:
        if f["id"] in contributors:
            continue
        if f.get("frozen"):
            continue
        loss = max(1, int(f["grain"] * loss_frac))
        ctx.update_fief(f["id"], grain=max(0, f["grain"] - loss))
        loss_note.append(f["name"])

    ctx.update_event(ctx.event_id, status="resolved")
    who = ", ".join(loss_note[:8]) if loss_note else "-"
    return (
        f"☠️ Ночь бандитов: провал ({total_might}/{threshold} силы). "
        f"Пострадали: {who}."
    )


def _resolve_cattle_plague(eff: dict[str, Any], ctx: CatastropheResolveCtx) -> str:
    del eff
    ctx.update_event(ctx.event_id, status="resolved")
    return "Мор скота отступил. Поля снова дышат."


_CATASTROPHE_RESOLVE_HANDLERS: dict[
    str, Callable[[dict[str, Any], CatastropheResolveCtx], str]
] = {
    "bandit_night": _resolve_bandit_night,
    "cattle_plague": _resolve_cattle_plague,
}


def resolve_catastrophe(key: str, ctx: CatastropheResolveCtx) -> str:
    """Resolve отгруженной катастрофы; неизвестный ключ - мягкое закрытие."""
    handler = _CATASTROPHE_RESOLVE_HANDLERS.get(key)
    if handler is None:
        ctx.update_event(ctx.event_id, status="resolved")
        meta = CATASTROPHES.get(key) or {}
        name = meta.get("name_ru", key)
        return f"Катастрофа \"{name}\" завершилась."
    return handler(catastrophe_effect(key), ctx)
