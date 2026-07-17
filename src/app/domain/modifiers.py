"""Композируемый слой модификаторов: виды, области, длительность, провайдер из storage."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from app.domain.events import catastrophe_effect, minor_effect

if TYPE_CHECKING:
    from app.domain.tile_entities import ActiveTileEntityRef


class EffectKind(str, Enum):
    """Виды ongoing-эффектов, которые сегодня читают production/raid/trade/build."""

    FARM_MULT = "farm_mult"
    FOG_IGNORES_PATROL = "fog_ignores_patrol"
    TRADE_BONUS_FRAC = "trade_bonus_frac"
    UPGRADE_COST_MULT = "upgrade_cost_mult"
    TRADE_GIFT_GRAIN = "trade_gift_grain"


class ModifierScope(str, Enum):
    WORLD = "world"
    REALM = "realm"
    FIEF = "fief"
    TILE = "tile"


class ComposeRule(str, Enum):
    MULTIPLY = "multiply"
    OR_FLAGS = "or_flags"
    ADD = "add"


COMPOSE_RULES: dict[EffectKind, ComposeRule] = {
    EffectKind.FARM_MULT: ComposeRule.MULTIPLY,
    EffectKind.UPGRADE_COST_MULT: ComposeRule.MULTIPLY,
    EffectKind.FOG_IGNORES_PATROL: ComposeRule.OR_FLAGS,
    EffectKind.TRADE_BONUS_FRAC: ComposeRule.ADD,
    EffectKind.TRADE_GIFT_GRAIN: ComposeRule.ADD,
}

# Имя метода ModifierSet для каждого kind (reachability / контракты).
MODIFIER_SET_KIND_READERS: dict[EffectKind, str] = {
    EffectKind.FARM_MULT: "farm_mult",
    EffectKind.FOG_IGNORES_PATROL: "fog_ignores_patrol",
    EffectKind.TRADE_BONUS_FRAC: "trade_bonus_frac",
    EffectKind.UPGRADE_COST_MULT: "upgrade_cost_mult",
    EffectKind.TRADE_GIFT_GRAIN: "trade_gift_grain",
}

# Kinds, которые live-пути Engine/handlers реально читают.
LIVE_READ_MODIFIER_KINDS: frozenset[EffectKind] = frozenset(MODIFIER_SET_KIND_READERS)


@dataclass(frozen=True)
class Modifier:
    """Один дескриптор эффекта с областью и длительностью."""

    kind: EffectKind
    scope: ModifierScope
    source_key: str
    value: float | int | bool
    # Метаданные срока: None - нет resolves_tick; иначе resolves_tick - tick_index (может быть <0).
    # Live-чтение не отфильтровывает по этому полю: авторитет - наличие active-строки в storage.
    ticks_remaining: int | None = None
    target_id: int | None = None


@dataclass(frozen=True)
class ActiveCatastropheRef:
    """Активная catastrophe-строка: ключ и resolves_tick из realm_events."""

    key: str
    resolves_tick: int | None = None


@dataclass(frozen=True)
class RealmModifierCtx:
    """Снимок storage для чистого collect (без запросов к БД внутри коллектора)."""

    active_minor_key: str | None = None
    active_catastrophes: Sequence[ActiveCatastropheRef] = ()
    # Ссылки на active tile_entities; пустой tuple - тот же ModifierSet, что без поля.
    active_tile_entities: Sequence[ActiveTileEntityRef] = ()
    tick_index: int = 0
    scope: ModifierScope = ModifierScope.REALM
    fief_id: int | None = None
    tile_id: int | None = None


@dataclass(frozen=True)
class ModifierSet:
    modifiers: tuple[Modifier, ...] = ()

    def __iter__(self):
        return iter(self.modifiers)

    def __len__(self) -> int:
        return len(self.modifiers)

    def with_modifiers(self, extra: Iterable[Modifier]) -> ModifierSet:
        return ModifierSet(self.modifiers + tuple(extra))

    def filter_scope(
        self,
        scope: ModifierScope,
        *,
        target_id: int | None = None,
    ) -> ModifierSet:
        """Оставляет модификаторы нужной области; для fief/tile - ещё и target_id."""
        out: list[Modifier] = []
        for mod in self.modifiers:
            if mod.scope != scope:
                continue
            if scope in (ModifierScope.FIEF, ModifierScope.TILE):
                if target_id is not None and mod.target_id not in (None, target_id):
                    continue
            out.append(mod)
        return ModifierSet(tuple(out))

    def exclude_expired(self) -> ModifierSet:
        """Опциональный фильтр по ticks_remaining < 0 (не используется live-путями)."""
        return ModifierSet(
            tuple(
                m
                for m in self.modifiers
                if m.ticks_remaining is None or int(m.ticks_remaining) >= 0
            )
        )

    def _of_kind(self, kind: EffectKind) -> tuple[Modifier, ...]:
        # Presence-authoritative: не режем по ticks_remaining на live compose.
        return tuple(m for m in self.modifiers if m.kind == kind)

    def compose(self, kind: EffectKind) -> float | int | bool:
        """Составное значение по COMPOSE_RULES (единственный источник правил)."""
        rule = COMPOSE_RULES[kind]
        mods = self._of_kind(kind)
        if rule is ComposeRule.MULTIPLY:
            return _compose_multiply((float(m.value) for m in mods), identity=1.0)
        if rule is ComposeRule.OR_FLAGS:
            return any(bool(m.value) for m in mods)
        if rule is ComposeRule.ADD:
            return _compose_add((float(m.value) for m in mods), identity=0.0)
        raise RuntimeError(f"Неизвестное правило композиции: {rule}")

    def farm_mult(self) -> float:
        return float(self.compose(EffectKind.FARM_MULT))

    def fog_ignores_patrol(self) -> bool:
        return bool(self.compose(EffectKind.FOG_IGNORES_PATROL))

    def trade_bonus_frac(self) -> float:
        return float(self.compose(EffectKind.TRADE_BONUS_FRAC))

    def upgrade_cost_mult(self) -> float:
        return float(self.compose(EffectKind.UPGRADE_COST_MULT))

    def trade_gift_grain(self) -> int:
        return int(self.compose(EffectKind.TRADE_GIFT_GRAIN))


def _compose_multiply(values: Iterable[float], *, identity: float) -> float:
    result = identity
    for value in values:
        result *= value
    return result


def _compose_add(values: Iterable[float], *, identity: float) -> float:
    result = identity
    for value in values:
        result += value
    return result


def _coerce_value(kind: EffectKind, raw: Any) -> float | int | bool:
    if kind is EffectKind.FOG_IGNORES_PATROL:
        return bool(raw)
    if kind is EffectKind.TRADE_GIFT_GRAIN:
        return int(raw)
    return float(raw)


def _ongoing_field_to_kind() -> dict[str, EffectKind]:
    """Wiring поле→kind из контрактов (единственный источник для провайдера)."""
    from app.domain.event_contracts import ongoing_field_to_kind

    return ongoing_field_to_kind()


def modifiers_from_effect_dict(
    source_key: str,
    eff: dict[str, Any],
    *,
    scope: ModifierScope = ModifierScope.REALM,
    ticks_remaining: int | None = None,
    target_id: int | None = None,
    field_kinds: dict[str, EffectKind] | None = None,
) -> tuple[Modifier, ...]:
    """Строит ongoing-модификаторы по wiring из контрактов."""
    mapping = field_kinds if field_kinds is not None else _ongoing_field_to_kind()
    out: list[Modifier] = []
    for field_name, kind in mapping.items():
        if field_name not in eff:
            continue
        out.append(
            Modifier(
                kind=kind,
                scope=scope,
                source_key=source_key,
                value=_coerce_value(kind, eff[field_name]),
                ticks_remaining=ticks_remaining,
                target_id=target_id,
            )
        )
    return tuple(out)


def _minor_ticks_remaining(eff: dict[str, Any]) -> int | None:
    if "duration_ticks" not in eff:
        return None
    return int(eff["duration_ticks"])


def _catastrophe_ticks_remaining(
    resolves_tick: int | None, tick_index: int
) -> int | None:
    if resolves_tick is None:
        return None
    return int(resolves_tick) - int(tick_index)


def modifiers_from_minor_key(
    key: str | None,
    *,
    scope: ModifierScope = ModifierScope.REALM,
) -> tuple[Modifier, ...]:
    if not key:
        return ()
    try:
        eff = minor_effect(key)
    except KeyError:
        return ()
    return modifiers_from_effect_dict(
        key,
        eff,
        scope=scope,
        ticks_remaining=_minor_ticks_remaining(eff),
    )


def modifiers_from_catastrophe_key(
    key: str | None,
    *,
    scope: ModifierScope = ModifierScope.REALM,
    ticks_remaining: int | None = None,
) -> tuple[Modifier, ...]:
    if not key:
        return ()
    try:
        eff = catastrophe_effect(key)
    except KeyError:
        return ()
    return modifiers_from_effect_dict(
        key, eff, scope=scope, ticks_remaining=ticks_remaining
    )


def collect_active_modifiers(ctx: RealmModifierCtx) -> ModifierSet:
    """Выводит ModifierSet из minor/catastrophe/tile_entities (чистая функция)."""
    from app.domain.tile_entities import modifiers_from_tile_entities

    parts: list[Modifier] = []
    parts.extend(
        modifiers_from_minor_key(ctx.active_minor_key, scope=ctx.scope)
    )
    for ref in ctx.active_catastrophes or ():
        ticks = _catastrophe_ticks_remaining(ref.resolves_tick, ctx.tick_index)
        parts.extend(
            modifiers_from_catastrophe_key(
                ref.key,
                scope=ctx.scope,
                ticks_remaining=ticks,
            )
        )
    if ctx.active_tile_entities:
        parts.extend(
            modifiers_from_tile_entities(
                ctx.active_tile_entities,
                tick_index=ctx.tick_index,
            )
        )
    result = ModifierSet(tuple(parts))
    if ctx.scope in (ModifierScope.FIEF, ModifierScope.TILE) and (
        ctx.fief_id is not None or ctx.tile_id is not None
    ):
        target = ctx.tile_id if ctx.scope is ModifierScope.TILE else ctx.fief_id
        result = result.filter_scope(ctx.scope, target_id=target)
    elif ctx.scope is not ModifierScope.REALM:
        result = result.filter_scope(ctx.scope)
    # Не exclude_expired: active catastrophe с overdue resolves_tick всё ещё действует.
    return result
