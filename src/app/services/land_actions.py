"""Земля и постройки: claim/build/demolish/gather/patrol + онбординг-квесты."""
from __future__ import annotations

from app.repos import LandActionRepos

import random

from app import balance as B
from app.domain import absence as absence_mod
from app.domain.map_geometry import adjacent_claimable

from app.domain.map_gen import coord_label
from app.domain.hunger import gather_might_hungry_message
from app.domain.resource_bags import apply_gather_to_stash, stash_from_row
from app.domain.resource_format import (
    gather_forbidden_message,
    gather_result_text,
)
from app.domain.resource_registry import live_resource_keys



def try_complete_onboard_claim(fief: dict) -> dict | None:
    """Шаг 2: занятие земли → шаг 3 и награда товарами. Идемпотентно."""
    if int(fief["onboard_step"]) != 2:
        return None
    return {
        "onboard_step": 3,
        "goods": int(fief["goods"]) + B.ONBOARD_DAY2_GOODS,
    }


def try_complete_onboard_build(fief: dict) -> dict | None:
    """Шаг 3: строительство → шаг 4 и награда товарами. Идемпотентно."""
    if int(fief["onboard_step"]) != 3:
        return None
    return {
        "onboard_step": 4,
        "goods": int(fief["goods"]) + B.ONBOARD_DAY3_GOODS,
    }


class LandActionService:
    def __init__(self, engine, db: LandActionRepos) -> None:
        self._engine = engine
        self._db = db

    def demolish_options(self, fief_id: int) -> list[dict]:
        """Незаросшие клетки усадьбы для UI сноса."""
        return [
            t
            for t in self._db.fief_tiles(fief_id)
            if not t.get("is_overgrown")
        ]

    def build_options(self, fief_id: int) -> tuple[list[dict], float]:
        """Клетки и множитель стоимости для UI стройки/апгрейда."""
        tiles = self.demolish_options(fief_id)
        fief = self._db.get_fief(fief_id)
        if not fief:
            return tiles, 1.0
        realm = self._engine.get_realm(int(fief["realm_id"]))
        if not realm:
            return tiles, 1.0
        cost_mult = self._engine.realm_modifiers(realm).upgrade_cost_mult()
        return tiles, cost_mult

    def claim_tile(self, fief_id: int, x: int, y: int) -> str:
        fief = self._db.get_fief(fief_id)
        self._engine.collect_for_fief(fief_id)
        fief = self._db.get_fief(fief_id)
        tiles = self._db.fief_tiles(fief_id)
        n = len([t for t in tiles if not t.get("is_overgrown")]) + 1
        if n > B.TILE_HARD_CAP:
            raise ValueError("Достигнут предел клеток")
        realm_id = fief["realm_id"]
        target = self._db.get_tile(realm_id, x, y)
        if not target:
            raise ValueError("Клетка не существует")
        if target["owner_fief_id"] is not None and not target.get("is_overgrown"):
            raise ValueError("Клетка занята")
        realm = self._db.get_realm(realm_id)
        views = {(t.x, t.y): t for t in self._engine.tile_views(realm_id)}
        owned = {(t["x"], t["y"]) for t in tiles if not t.get("is_overgrown")}
        if (x, y) not in adjacent_claimable(
            owned,
            views,
            width=realm["width"],
            height=realm["height"],
            for_fief_id=fief_id,
        ):
            raise ValueError("Клетка не соседняя")

        is_wilds = target["tile_type"] == B.TILE_WILDS
        barn = self._engine.barn_level(fief_id)
        if target.get("is_overgrown"):
            prev = target.get("owner_fief_id")
            cost = B.claim_cost(n, is_wilds=False)
            gate = B.claim_stash_gate_message(cost, barn)
            if gate:
                raise ValueError(gate)
            if fief["goods"] < cost:
                raise ValueError(f"Нужно {cost} товаров")
            with self._db.transaction():
                self._engine._spend_action(fief)
                if not self._db.debit_fief_resources(fief_id, goods=cost):
                    raise ValueError(f"Нужно {cost} товаров")
                if prev and prev != fief_id:
                    comp = absence_mod.compensation_for_claim(cost)
                    prev_f = self._db.get_fief(prev)
                    if prev_f:
                        self._db.update_fief(prev, goods=prev_f["goods"] + comp)
                self._db.update_tile(
                    target["id"],
                    owner_fief_id=fief_id,
                    is_overgrown=False,
                    is_core=(n <= 2),
                    building=None,
                    building_level=0,
                    damaged=False,
                )
                if n == 2:
                    for t in self._db.fief_tiles(fief_id):
                        self._db.update_tile(t["id"], is_core=True)
                self._engine.maybe_grow_map(realm_id)
            self._engine._onboard_claim(fief_id)
            return f"Занята заросшая клетка {coord_label(x, y)} (−{cost} товаров)."

        cost = B.claim_cost(n, is_wilds=is_wilds)
        gate = B.claim_stash_gate_message(cost, barn)
        if gate:
            raise ValueError(gate)
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров (у вас {fief['goods']})")

        new_type = target["tile_type"]
        ruins_loot = 0
        ruins_loot_added = 0
        if is_wilds:
            new_type = random.choice(B.WILDS_CLEAR_TO)
        if new_type == B.TILE_RUINS and not target.get("ruins_looted"):
            ruins_loot = random.randint(B.RUINS_LOOT_MIN, B.RUINS_LOOT_MAX)

        with self._db.transaction():
            self._engine._spend_action(fief)
            if not self._db.debit_fief_resources(fief_id, goods=cost):
                raise ValueError(f"Нужно {cost} товаров (у вас {fief['goods']})")

            if ruins_loot:
                fief = self._db.get_fief(fief_id)
                cap = B.stash_cap(self._engine.barn_level(fief_id))
                ruins_loot_added = min(ruins_loot, max(0, cap - fief["goods"]))
                self._db.update_fief(fief_id, goods=fief["goods"] + ruins_loot_added)

            self._db.update_tile(
                target["id"],
                owner_fief_id=fief_id,
                tile_type=new_type,
                is_core=(n <= 2),
                ruins_looted=True if new_type == B.TILE_RUINS or target.get("ruins_looted") else target.get("ruins_looted"),
                is_overgrown=False,
            )
            if n == 2:
                for t in self._db.fief_tiles(fief_id):
                    self._db.update_tile(t["id"], is_core=True)

            self._engine.maybe_grow_map(realm_id)
        self._engine._onboard_claim(fief_id)
        extra = ""
        if ruins_loot:
            cap_hint = (
                "" if ruins_loot_added == ruins_loot else " (склад почти полон)"
            )
            extra = (
                f" Находка в руинах: +{ruins_loot_added} товаров{cap_hint}. "
                f"Дальше +{B.RUINS_PASSIVE_GRAIN} зерна и "
                f"+{B.RUINS_PASSIVE_GOODS} товаров/день."
            )
        if is_wilds:
            extra = f" Глушь расчищена → {B.TILE_NAMES_RU[new_type]}." + extra
        return f"Клетка {coord_label(x, y)} присоединена (−{cost} товаров).{extra}"

    def build_or_upgrade(self, fief_id: int, x: int, y: int, building: str) -> str:
        if building not in B.BUILDING_COSTS:
            raise ValueError("Неизвестное здание")
        fief = self._db.get_fief(fief_id)
        self._engine.collect_for_fief(fief_id)
        fief = self._db.get_fief(fief_id)
        tile = self._db.get_tile(fief["realm_id"], x, y)
        if not tile or tile["owner_fief_id"] != fief_id:
            raise ValueError("Это не ваша клетка")
        if tile.get("is_overgrown"):
            raise ValueError("Клетка заросла")
        if tile["tile_type"] == B.TILE_WILDS:
            raise ValueError("Сначала расчистите глушь (займите клетку)")

        current = tile.get("building")
        level = int(tile.get("building_level") or 0)
        damaged = bool(tile.get("damaged"))

        if damaged and current:
            # ремонт = половина стоимости текущего уровня
            cost = B.repair_cost(current, level)
            if fief["goods"] < cost:
                raise ValueError(f"Ремонт: нужно {cost} товаров")
            with self._db.transaction():
                self._engine._spend_action(fief)
                if not self._db.debit_fief_resources(fief_id, goods=cost):
                    raise ValueError(f"Ремонт: нужно {cost} товаров")
                self._db.update_tile(tile["id"], damaged=False)
            self._engine._onboard_build(fief_id)
            return f"Отремонтирован {B.BUILDING_NAMES_RU[current]} {level} (−{cost} товаров)."

        if current == B.BLD_MANOR:
            raise ValueError("Двор - главная клетка, его нельзя заменить")
        if building == B.BLD_MANOR:
            raise ValueError("Двор ставится только при основании усадьбы")
        if building not in B.PLAYER_BUILDINGS:
            raise ValueError("Неизвестное здание")
        if current and current != building:
            raise ValueError("На клетке уже другое здание - сначала снесите его")
        if not current:
            target_level = 1
        else:
            target_level = level + 1
        if target_level > 3:
            raise ValueError("Максимальный уровень")
        # если damaged сбросили выше; апгрейд
        cost = B.building_upgrade_cost(building, target_level)
        realm = self._db.get_realm(fief["realm_id"])
        # Снимок катастроф до write-транзакции: collect чистый, без commit внутри tx.
        build_mods = self._engine.realm_modifiers(realm)
        cost = B.scaled_building_cost(cost, build_mods.upgrade_cost_mult())
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров")
        with self._db.transaction():
            self._engine._spend_action(fief)
            if not self._db.debit_fief_resources(fief_id, goods=cost):
                raise ValueError(f"Нужно {cost} товаров")
            self._db.update_tile(
                tile["id"],
                building=building,
                building_level=target_level,
                damaged=False,
            )
        self._engine._onboard_build(fief_id)
        return f"{B.BUILDING_NAMES_RU[building]} {target_level} на {coord_label(x, y)} (−{cost} товаров)."

    def demolish_building(self, fief_id: int, x: int, y: int) -> str:
        """Снос здания на клетке без траты действия; возврат доли вложенных товаров."""
        fief = self._engine.require_active_fief(fief_id)
        if fief.get("frozen"):
            raise ValueError("Усадьба заморожена")
        self._engine._require_action_window(int(fief["realm_id"]))
        tile = self._db.get_tile(fief["realm_id"], x, y)
        if not tile or tile["owner_fief_id"] != fief_id:
            raise ValueError("Это не ваша клетка")
        if tile.get("is_overgrown"):
            raise ValueError("Клетка заросла")
        building = tile.get("building")
        level = int(tile.get("building_level") or 0)
        if not building or level <= 0:
            raise ValueError("На клетке нет здания")
        if building == B.BLD_MANOR:
            raise ValueError("Двор снести нельзя")
        if building not in B.BUILDING_COSTS:
            raise ValueError("Это здание нельзя снести")
        refund = B.demolish_refund_goods(building, level)
        with self._db.transaction():
            self._db.credit_fief_resources(fief_id, goods=refund)
            self._db.update_tile(
                tile["id"],
                building=None,
                building_level=0,
                damaged=False,
            )
        name = B.BUILDING_NAMES_RU.get(building, building)
        return (
            f"Снесено: {name} {level} на {coord_label(x, y)}. "
            f"Возврат {refund} товаров ({int(B.DEMOLISH_REFUND_FRAC * 100)}%)."
        )

    def gather_resource(self, fief_id: int, resource: str) -> str:
        """Потратить 1 действие на плоский сбор одного ресурса."""
        if resource not in live_resource_keys():
            raise ValueError(gather_forbidden_message())
        fief = self._db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        if fief.get("frozen"):
            raise ValueError("Усадьба заморожена")
        if resource == B.RES_MIGHT and fief.get("hungry"):
            raise ValueError(gather_might_hungry_message())
        amount = B.gather_amount(resource)
        self._engine.collect_for_fief(fief_id, include_might=(resource != B.RES_MIGHT))
        fief = self._db.get_fief(fief_id)
        with self._db.transaction():
            self._engine._spend_action(fief)
            fief = self._db.get_fief(fief_id)
            barn = self._engine.barn_level(fief_id)
            cap = B.stash_cap(barn)
            stash, gained = apply_gather_to_stash(
                stash_from_row(fief), resource, amount, cap=cap
            )
            self._db.update_fief(fief_id, **{resource: stash[resource]})
            return gather_result_text(resource, gained, amount)

    def disband_militia(self, fief_id: int, keep: int) -> str:
        """Добровольно сократить дружину до keep. Без траты действия."""
        fief = self._db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        if fief.get("frozen"):
            raise ValueError("Усадьба заморожена")
        # Сначала урожай в stash, иначе status_card после роспуска вернёт силу из pending.
        self._engine.collect_for_fief(fief_id)
        fief = self._db.get_fief(fief_id)
        current = int(fief.get("might") or 0)
        if current <= 0:
            raise ValueError("Некого распускать")
        new_might, lost = B.militia_after_disband(current, keep)
        if lost <= 0:
            raise ValueError("Некого распускать")
        prepaid = min(int(fief.get("militia_prepaid_might") or 0), new_might)
        self._db.update_fief(
            fief_id,
            might=new_might,
            militia_prepaid_might=prepaid,
        )
        feed = B.militia_upkeep_grain(
            B.militia_billable_might(new_might, prepaid)
        )
        return (
            f"Распустил {lost} (−{lost} Силы). "
            f"Дома {new_might}, корм дружины {feed} зерна/день."
        )

    def _onboard_claim(self, fief_id: int) -> None:
        fief = self._db.get_fief(fief_id)
        patch = try_complete_onboard_claim(fief)
        if patch:
            self._db.update_fief(fief_id, **patch)

    def _onboard_build(self, fief_id: int) -> None:
        fief = self._db.get_fief(fief_id)
        patch = try_complete_onboard_build(fief)
        if patch:
            self._db.update_fief(fief_id, **patch)

    def patrol(self, fief_id: int) -> str:
        fief = self._db.get_fief(fief_id)
        cost = int(B.PATROL_COST_MIGHT)
        if cost > 0 and fief["might"] < cost:
            raise ValueError(f"Нужно {cost} силы")
        with self._db.transaction():
            self._engine._spend_action(fief)
            realm = self._db.get_realm(fief["realm_id"]) or {}
            tick_index = int(realm.get("tick_index") or 0)
            if cost > 0:
                if not self._db.debit_fief_resources(fief_id, might=cost):
                    raise ValueError(f"Нужно {cost} силы")
            self._db.update_fief(
                fief_id,
                patrol_until=None,
                patrol_until_tick=tick_index + B.PATROL_TICKS,
            )
        if cost > 0:
            return (
                f"Дозор выставлен на {B.PATROL_TICKS} тик(а) "
                f"(−{cost} силы)."
            )
        return f"Дозор выставлен на {B.PATROL_TICKS} тик(а)."
