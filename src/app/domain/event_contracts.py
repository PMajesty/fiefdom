"""Контракты эффектов: декларация wiring для каждого отгруженного события."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domain.events import (
    SHIPPED_CATASTROPHE_KEYS,
    SHIPPED_MINOR_KEYS,
    catastrophe_effect,
    minor_effect,
)
from app.domain.modifiers import (
    COMPOSE_RULES,
    LIVE_READ_MODIFIER_KINDS,
    MODIFIER_SET_KIND_READERS,
    EffectKind,
)

FieldConsumer = Literal[
    "instant",
    "ongoing",
    "resolve",
    "duration",
    "flavor",
]


@dataclass(frozen=True)
class OngoingModifierDecl:
    """Wiring: поле effect-таблицы → EffectKind (провайдер читает отсюда)."""

    effect_field: str
    kind: EffectKind


@dataclass(frozen=True)
class EffectContract:
    """Полная декларация эффекта ключа: instant / ongoing / resolve."""

    key: str
    source: Literal["minor", "catastrophe"]
    has_instant_handler: bool
    has_resolve_handler: bool
    interactive: bool
    ongoing: tuple[OngoingModifierDecl, ...]
    field_consumers: dict[str, FieldConsumer]

    @property
    def consumed_fields(self) -> frozenset[str]:
        return frozenset(self.field_consumers)


def _ongoing(field: str, kind: EffectKind) -> OngoingModifierDecl:
    return OngoingModifierDecl(effect_field=field, kind=kind)


def _minor(
    key: str,
    *,
    instant: bool = False,
    ongoing: tuple[OngoingModifierDecl, ...] = (),
    fields: dict[str, FieldConsumer],
) -> EffectContract:
    return EffectContract(
        key=key,
        source="minor",
        has_instant_handler=instant,
        has_resolve_handler=False,
        interactive=False,
        ongoing=ongoing,
        field_consumers=fields,
    )


def _catastrophe(
    key: str,
    *,
    resolve: bool = False,
    interactive: bool = False,
    ongoing: tuple[OngoingModifierDecl, ...] = (),
    fields: dict[str, FieldConsumer],
) -> EffectContract:
    return EffectContract(
        key=key,
        source="catastrophe",
        has_instant_handler=False,
        has_resolve_handler=resolve,
        interactive=interactive,
        ongoing=ongoing,
        field_consumers=fields,
    )


EFFECT_CONTRACTS: dict[str, EffectContract] = {
    "harvest": _minor(
        "harvest",
        ongoing=(_ongoing("farm_mult", EffectKind.FARM_MULT),),
        fields={"farm_mult": "ongoing", "duration_ticks": "duration"},
    ),
    "fog": _minor(
        "fog",
        ongoing=(_ongoing("raids_ignore_patrol", EffectKind.FOG_IGNORES_PATROL),),
        fields={"raids_ignore_patrol": "ongoing", "duration_ticks": "duration"},
    ),
    "rats": _minor(
        "rats",
        instant=True,
        fields={"unprot_grain_threshold": "instant", "loss_frac": "instant"},
    ),
    "fair": _minor(
        "fair",
        ongoing=(_ongoing("trade_bonus_frac", EffectKind.TRADE_BONUS_FRAC),),
        fields={"trade_bonus_frac": "ongoing", "duration_ticks": "duration"},
    ),
    "good_stone": _minor(
        "good_stone",
        ongoing=(_ongoing("upgrade_cost_mult", EffectKind.UPGRADE_COST_MULT),),
        fields={"upgrade_cost_mult": "ongoing", "duration_ticks": "duration"},
    ),
    "drought": _minor(
        "drought",
        ongoing=(_ongoing("farm_mult", EffectKind.FARM_MULT),),
        fields={"farm_mult": "ongoing", "duration_ticks": "duration"},
    ),
    "wedding": _minor(
        "wedding",
        ongoing=(_ongoing("trade_gift_grain", EffectKind.TRADE_GIFT_GRAIN),),
        fields={"trade_gift_grain": "ongoing", "duration_ticks": "duration"},
    ),
    "omen": _minor(
        "omen",
        fields={"foreshadow": "flavor"},
    ),
    "blight": _minor(
        "blight",
        instant=True,
        fields={"goods_loss_frac": "instant"},
    ),
    "press_gang": _minor(
        "press_gang",
        instant=True,
        fields={"might_loss": "instant"},
    ),
    "fire": _minor(
        "fire",
        instant=True,
        fields={"damage_random_building": "instant"},
    ),
    "toll": _minor(
        "toll",
        instant=True,
        fields={"goods_flat_loss": "instant"},
    ),
    "spoilage": _minor(
        "spoilage",
        instant=True,
        fields={"grain_loss_frac": "instant"},
    ),
    "bandit_night": _catastrophe(
        "bandit_night",
        resolve=True,
        interactive=True,
        fields={
            "might_per_player": "resolve",
            "loot_goods_per_player": "resolve",
            "fail_unprot_grain_frac": "resolve",
        },
    ),
    "cattle_plague": _catastrophe(
        "cattle_plague",
        resolve=True,
        ongoing=(_ongoing("farm_mult", EffectKind.FARM_MULT),),
        fields={"farm_mult": "ongoing"},
    ),
}


def shipped_contract_keys() -> frozenset[str]:
    return frozenset(SHIPPED_MINOR_KEYS) | frozenset(SHIPPED_CATASTROPHE_KEYS)


def ongoing_field_to_kind() -> dict[str, EffectKind]:
    """Единый wiring поле→kind из всех контрактов (для провайдера модификаторов)."""
    out: dict[str, EffectKind] = {}
    for contract in EFFECT_CONTRACTS.values():
        for decl in contract.ongoing:
            prev = out.get(decl.effect_field)
            if prev is not None and prev is not decl.kind:
                raise RuntimeError(
                    f"конфликт wiring для поля {decl.effect_field}: "
                    f"{prev} vs {decl.kind}"
                )
            out[decl.effect_field] = decl.kind
    return out


def validate_effect_contracts() -> list[str]:
    """Проверяет полноту контрактов и отсутствие мертвых полей. Пустой список = ок."""
    from app.domain.event_apply import (
        INSTANT_MINOR_HANDLER_KEYS,
        RESOLVE_CATASTROPHE_HANDLER_KEYS,
    )

    errors: list[str] = []
    expected = shipped_contract_keys()
    registered = frozenset(EFFECT_CONTRACTS)

    for key in sorted(expected - registered):
        errors.append(f"нет контракта для отгруженного ключа: {key}")
    for key in sorted(registered - expected):
        errors.append(f"контракт вне отгруженного пула: {key}")

    if frozenset(COMPOSE_RULES) != frozenset(MODIFIER_SET_KIND_READERS):
        errors.append("COMPOSE_RULES и MODIFIER_SET_KIND_READERS расходятся")
    if LIVE_READ_MODIFIER_KINDS != frozenset(MODIFIER_SET_KIND_READERS):
        errors.append("LIVE_READ_MODIFIER_KINDS не совпадает с ModifierSet API")

    for key in sorted(expected & registered):
        contract = EFFECT_CONTRACTS[key]
        if contract.source == "minor":
            table = set(minor_effect(key))
        else:
            table = set(catastrophe_effect(key))
        declared = set(contract.consumed_fields)
        for field_name in sorted(table - declared):
            errors.append(
                f"{key}: поле effect-таблицы без потребителя: {field_name}"
            )
        for field_name in sorted(declared - table):
            errors.append(
                f"{key}: контракт ссылается на отсутствующее поле: {field_name}"
            )

        ongoing_fields = {d.effect_field for d in contract.ongoing}
        for field_name, consumer in contract.field_consumers.items():
            if consumer == "ongoing" and field_name not in ongoing_fields:
                errors.append(
                    f"{key}: поле {field_name} помечено ongoing, но нет OngoingModifierDecl"
                )
        for decl in contract.ongoing:
            if contract.field_consumers.get(decl.effect_field) != "ongoing":
                errors.append(
                    f"{key}: OngoingModifierDecl {decl.effect_field} без consumer=ongoing"
                )
            if decl.kind not in MODIFIER_SET_KIND_READERS:
                errors.append(
                    f"{key}: ongoing kind {decl.kind} нет в ModifierSet API"
                )
            if decl.kind not in LIVE_READ_MODIFIER_KINDS:
                errors.append(
                    f"{key}: ongoing kind {decl.kind} не читается live-путями"
                )

        if contract.source == "minor":
            has_handler = key in INSTANT_MINOR_HANDLER_KEYS
            if contract.has_instant_handler and not has_handler:
                errors.append(f"{key}: контракт требует instant handler")
            if has_handler and not contract.has_instant_handler:
                errors.append(f"{key}: есть instant handler без флага в контракте")
            if contract.has_resolve_handler:
                errors.append(f"{key}: минор не должен иметь resolve handler")
        else:
            has_handler = key in RESOLVE_CATASTROPHE_HANDLER_KEYS
            if contract.has_resolve_handler and not has_handler:
                errors.append(f"{key}: контракт требует resolve handler")
            if has_handler and not contract.has_resolve_handler:
                errors.append(f"{key}: есть resolve handler без флага в контракте")
            if contract.has_instant_handler:
                errors.append(f"{key}: катастрофа не должна иметь instant handler")

    for key in SHIPPED_CATASTROPHE_KEYS:
        if key not in RESOLVE_CATASTROPHE_HANDLER_KEYS:
            errors.append(f"{key}: отгруженная катастрофа без resolve handler")

    try:
        ongoing_field_to_kind()
    except RuntimeError as exc:
        errors.append(str(exc))

    from app.domain.tile_entities import validate_entity_kind_contracts

    errors.extend(validate_entity_kind_contracts())
    return errors
