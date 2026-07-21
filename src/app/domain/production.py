"""Tile production: TileView, Production bag, daily yields."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app import balance as B
from app.domain.resource_bags import add_bags, scale_bag
from app.domain.resource_registry import live_resource_keys


@dataclass
class TileView:
    x: int
    y: int
    tile_type: str
    owner_fief_id: int | None
    building: str | None
    building_level: int
    is_bridge: bool = False
    is_core: bool = False
    is_overgrown: bool = False


class Production:
    """Дневное производство: сумка по ключам реестра + defense.

    Мутация сумки - только через with_amounts / scale / plus.
    Свойство bag - read-only снимок (MappingProxyType).
    """

    __slots__ = ("_bag", "defense")

    def __init__(self, *, defense: float = 0.0, **amounts: float) -> None:
        self.defense = float(defense)
        self._bag = {
            key: float(amounts.get(key, 0) or 0) for key in live_resource_keys()
        }

    @property
    def bag(self) -> Mapping[str, float]:
        return MappingProxyType(self._bag)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Production):
            return NotImplemented
        return self.defense == other.defense and self._bag == other._bag

    def __repr__(self) -> str:
        parts = ", ".join(f"{k}={v!r}" for k, v in self._bag.items())
        return f"Production({parts}, defense={self.defense!r})"

    def resources(self) -> dict[str, float]:
        return dict(self._bag)

    @classmethod
    def from_resources(
        cls, bag: Mapping[str, float], *, defense: float = 0.0
    ) -> "Production":
        return cls(
            defense=float(defense),
            **{key: float(bag.get(key, 0) or 0) for key in live_resource_keys()},
        )

    def with_amounts(self, **changes: float) -> "Production":
        bag = dict(self._bag)
        defense = self.defense
        if "defense" in changes:
            defense = float(changes.pop("defense"))
        for key, value in changes.items():
            bag[key] = float(value)
        return Production(defense=defense, **bag)

    def scale(self, mult: float) -> "Production":
        return Production.from_resources(
            scale_bag(self.resources(), mult), defense=self.defense
        )

    def plus(self, other: "Production") -> "Production":
        return Production.from_resources(
            add_bags(self.resources(), other.resources()),
            defense=self.defense + other.defense,
        )


def tile_passive(tile_type: str) -> Production:
    if tile_type == B.TILE_RIVER:
        return Production(**{B.RES_GRAIN: B.RIVER_PASSIVE_GRAIN})
    if tile_type == B.TILE_ROAD:
        return Production(**{B.RES_GOODS: B.ROAD_PASSIVE_GOODS})
    return Production()


def building_production(building: str, level: int, tile_type: str) -> Production:
    if level <= 0 or not building:
        return Production()
    if building == B.BLD_MANOR:
        return Production(
            **{
                B.RES_GRAIN: float(B.MANOR_GRAIN),
                B.RES_GOODS: float(B.MANOR_GOODS),
                B.RES_MIGHT: float(B.MANOR_MIGHT),
            }
        )
    native = B.NATIVE_TILE.get(building)
    bonus = B.NATIVE_BONUS if native and tile_type == native else 1.0
    if building == B.BLD_FARM:
        return Production(**{B.RES_GRAIN: B.FARM_YIELD[level] * bonus})
    if building == B.BLD_WORKSHOP:
        return Production(**{B.RES_GOODS: B.WORKSHOP_YIELD[level] * bonus})
    if building == B.BLD_WATCH:
        return Production(
            defense=B.WATCH_DEFENSE[level] * bonus,
            **{B.RES_MIGHT: B.WATCH_MIGHT[level] * bonus},
        )
    return Production()


def apply_hunger_to_yields(
    prod: Production, *, preserved_might: float = 0.0
) -> Production:
    """Урожай × голод; сила обнуляется, затем возвращается preserved_might (двор)."""
    cleared = prod.with_amounts(**{B.RES_MIGHT: 0.0})
    return cleared.scale(B.HUNGER_PRODUCTION_MULT).with_amounts(
        **{B.RES_MIGHT: float(preserved_might)}
    )


def fief_daily_production(
    tiles: list[TileView],
    *,
    hungry: bool = False,
    farm_mult: float = 1.0,
    current_might: int = 0,
) -> Production:
    total = Production()
    active_tiles = 0
    manor_might = 0.0
    for t in tiles:
        if t.is_overgrown:
            continue
        active_tiles += 1
        p = tile_passive(t.tile_type)
        b = building_production(t.building or "", t.building_level, t.tile_type)
        if t.building == B.BLD_FARM:
            b = b.with_amounts(
                **{B.RES_GRAIN: b.resources()[B.RES_GRAIN] * farm_mult}
            )
        if t.building == B.BLD_MANOR:
            manor_might += b.resources()[B.RES_MIGHT]
            b = b.with_amounts(**{B.RES_MIGHT: 0.0})
        total = total.plus(p).plus(b)
    # Сила двора только до бесплатного потолка дружины.
    free_room = max(0, B.MILITIA_FREE - max(0, int(current_might)))
    manor_applied = min(manor_might, float(free_room))
    if active_tiles > 0 and B.FIEF_BASE_GOODS:
        total = total.with_amounts(
            **{B.RES_GOODS: total.resources()[B.RES_GOODS] + B.FIEF_BASE_GOODS}
        )
    if hungry:
        # Сторожки молчат; двор добирает до потолка полностью (не вдвое).
        return apply_hunger_to_yields(total, preserved_might=manor_applied)
    return total.with_amounts(
        **{B.RES_MIGHT: total.resources()[B.RES_MIGHT] + manor_applied}
    )
