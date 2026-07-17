"""Субстрат сущностей на клетках: реестр kinds, tick-resolve, метки карты, модификаторы.

Текстовая карта (economy.render_map_parts) пока не рисует entity-метки: шов -
entity_map_marks() / map_mark_for_kind(); PNG и fingerprint уже принимают их.
С нулём строк в tile_entities все пути - no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from app.domain.modifiers import (
    LIVE_READ_MODIFIER_KINDS,
    MODIFIER_SET_KIND_READERS,
    EffectKind,
    Modifier,
    ModifierScope,
)

@dataclass(frozen=True)
class EntityModifierDecl:
    """Wiring: поле payload → EffectKind + область."""

    payload_field: str
    kind: EffectKind
    scope: ModifierScope = ModifierScope.TILE


@dataclass(frozen=True)
class EntityKindContract:
    """Декларация kind: tick handler, опциональная метка карты, модификаторы."""

    key: str
    has_tick_handler: bool
    map_mark: str | None = None
    modifiers: tuple[EntityModifierDecl, ...] = ()


@dataclass(frozen=True)
class ActiveTileEntityRef:
    """Активная строка tile_entities для провайдера модификаторов / fingerprint."""

    id: int
    kind: str
    x: int
    y: int
    payload: dict[str, Any]
    expires_tick: int | None = None


@dataclass(frozen=True)
class TileEntityResolveCtx:
    """Контекст tick-resolve: колбэки пишет Engine (без БД в домене)."""

    tick_index: int
    list_active: Callable[[], list[dict[str, Any]]]
    expire_entity: Callable[[int], dict[str, Any] | None]
    update_entity: Callable[..., None]


TileEntityTickHandler = Callable[
    [dict[str, Any], TileEntityResolveCtx], Sequence[str] | None
]

# Пустой реестр: контент не отгружен. Тесты регистрируют fake-kind временно.
ENTITY_KIND_CONTRACTS: dict[str, EntityKindContract] = {}
TICK_RESOLVE_HANDLERS: dict[str, TileEntityTickHandler] = {}


def entity_kind_contract(kind: str) -> EntityKindContract | None:
    return ENTITY_KIND_CONTRACTS.get(kind)


def tick_resolve_handler(kind: str) -> TileEntityTickHandler | None:
    return TICK_RESOLVE_HANDLERS.get(kind)


def map_mark_for_kind(kind: str) -> str | None:
    contract = entity_kind_contract(kind)
    if contract is None:
        return None
    mark = contract.map_mark
    if mark is None or mark == "":
        return None
    return mark


def tile_target_id(x: int, y: int) -> int:
    """Стабильный target_id для ModifierScope.TILE (без id строки map_tiles)."""
    return int(x) * 1_000_000 + int(y)


def entity_map_marks(entities: Sequence[dict[str, Any] | ActiveTileEntityRef]) -> list[
    tuple[int, int, str]
]:
    """Метки для PNG: (x, y, mark). Пустой список - не рисовать и не трогать fingerprint."""
    by_pos: dict[tuple[int, int], list[str]] = {}
    for raw in entities:
        if isinstance(raw, ActiveTileEntityRef):
            kind = raw.kind
            x, y = raw.x, raw.y
        else:
            kind = str(raw["kind"])
            x, y = int(raw["x"]), int(raw["y"])
        mark = map_mark_for_kind(kind)
        if mark is None:
            continue
        by_pos.setdefault((x, y), []).append(mark)
    out: list[tuple[int, int, str]] = []
    for (x, y), marks in sorted(by_pos.items()):
        out.append((x, y, "".join(sorted(marks))))
    return out


def entity_fingerprint_rows(
    entities: Sequence[dict[str, Any] | ActiveTileEntityRef],
) -> list[list[Any]]:
    """Компактные строки для map_fingerprint; пустой список - ключ entities опускается."""
    rows: list[list[Any]] = []
    normalized: list[tuple[int, int, int, str, dict[str, Any], int | None, str | None]] = []
    for raw in entities:
        if isinstance(raw, ActiveTileEntityRef):
            eid = raw.id
            x, y = raw.x, raw.y
            kind = raw.kind
            payload = dict(raw.payload or {})
            expires = raw.expires_tick
            mark = map_mark_for_kind(kind)
        else:
            eid = int(raw["id"])
            x, y = int(raw["x"]), int(raw["y"])
            kind = str(raw["kind"])
            payload = dict(raw.get("payload") or {})
            expires_raw = raw.get("expires_tick")
            expires = None if expires_raw is None else int(expires_raw)
            mark = map_mark_for_kind(kind)
        normalized.append((y, x, eid, kind, payload, expires, mark))
    for y, x, eid, kind, payload, expires, mark in sorted(normalized):
        rows.append([x, y, eid, kind, mark, payload, expires])
    return rows


def active_tile_entity_ref(row: dict[str, Any]) -> ActiveTileEntityRef:
    expires_raw = row.get("expires_tick")
    return ActiveTileEntityRef(
        id=int(row["id"]),
        kind=str(row["kind"]),
        x=int(row["x"]),
        y=int(row["y"]),
        payload=dict(row.get("payload") or {}),
        expires_tick=None if expires_raw is None else int(expires_raw),
    )


def _coerce_entity_value(kind: EffectKind, raw: Any) -> float | int | bool:
    if kind is EffectKind.FOG_IGNORES_PATROL:
        return bool(raw)
    if kind is EffectKind.TRADE_GIFT_GRAIN:
        return int(raw)
    return float(raw)


def modifiers_from_tile_entities(
    entities: Sequence[ActiveTileEntityRef],
    *,
    tick_index: int = 0,
) -> tuple[Modifier, ...]:
    """Ongoing-модификаторы из деклараций kind; presence = active-строка."""
    out: list[Modifier] = []
    for ent in entities:
        contract = entity_kind_contract(ent.kind)
        if contract is None:
            continue
        ticks_remaining: int | None = None
        if ent.expires_tick is not None:
            ticks_remaining = int(ent.expires_tick) - int(tick_index)
        for decl in contract.modifiers:
            if decl.payload_field not in ent.payload:
                continue
            target_id: int | None = None
            if decl.scope is ModifierScope.TILE:
                target_id = tile_target_id(ent.x, ent.y)
            elif decl.scope is ModifierScope.FIEF:
                fid = ent.payload.get("fief_id")
                target_id = None if fid is None else int(fid)
            out.append(
                Modifier(
                    kind=decl.kind,
                    scope=decl.scope,
                    source_key=ent.kind,
                    value=_coerce_entity_value(
                        decl.kind, ent.payload[decl.payload_field]
                    ),
                    ticks_remaining=ticks_remaining,
                    target_id=target_id,
                )
            )
    return tuple(out)


def resolve_realm_tile_entities(ctx: TileEntityResolveCtx) -> list[str]:
    """Один проход: expire по expires_tick, затем dispatch handlers. Без строк - []."""
    rows = ctx.list_active()
    if not rows:
        return []
    digest_lines: list[str] = []
    still_active: list[dict[str, Any]] = []
    for row in rows:
        expires = row.get("expires_tick")
        if expires is not None and int(expires) <= int(ctx.tick_index):
            ctx.expire_entity(int(row["id"]))
            continue
        still_active.append(row)
    for row in still_active:
        handler = tick_resolve_handler(str(row["kind"]))
        if handler is None:
            continue
        lines = handler(row, ctx)
        if lines:
            digest_lines.extend(str(line) for line in lines if line)
    return digest_lines


def validate_entity_kind_contracts() -> list[str]:
    """Полнота wiring entity kinds. Пустой реестр = ок; kind без handler - ошибка."""
    errors: list[str] = []
    registered = frozenset(ENTITY_KIND_CONTRACTS)
    handlers = frozenset(TICK_RESOLVE_HANDLERS)

    for key in sorted(handlers - registered):
        errors.append(f"tick handler без контракта: {key}")
    for key in sorted(registered):
        contract = ENTITY_KIND_CONTRACTS[key]
        if contract.key != key:
            errors.append(f"{key}: contract.key расходится с ключом реестра")
        has_handler = key in handlers
        if contract.has_tick_handler and not has_handler:
            errors.append(f"{key}: контракт требует tick handler")
        if has_handler and not contract.has_tick_handler:
            errors.append(f"{key}: есть tick handler без флага в контракте")
        if contract.map_mark is not None and contract.map_mark == "":
            errors.append(f"{key}: map_mark пустая строка (используйте None)")
        for decl in contract.modifiers:
            if decl.kind not in MODIFIER_SET_KIND_READERS:
                errors.append(
                    f"{key}: modifier kind {decl.kind} нет в ModifierSet API"
                )
            if decl.kind not in LIVE_READ_MODIFIER_KINDS:
                errors.append(
                    f"{key}: modifier kind {decl.kind} не читается live-путями"
                )
            if not decl.payload_field:
                errors.append(f"{key}: EntityModifierDecl без payload_field")
    return errors
