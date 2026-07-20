"""Онбординг: профиль, стартовые клетки, основание усадьбы.

Квест claim/build (_onboard_*) живёт в LandActionService - здесь только join/founding.
"""
from __future__ import annotations

from app.repos import OnboardingRepos

from app import balance as B
from app.domain.map_geometry import pick_max_separated_tiles, too_close_to_ruins

from app.domain.map_gen import coord_label
from app.domain.realm_identity import second_fief_on_world_message
from app.engine import fief_name_for_user


class OnboardingService:
    def __init__(self, engine, db: OnboardingRepos | None = None) -> None:
        self._engine = engine
        self._db = db if db is not None else engine.db

    def ensure_user(self, user) -> None:
        name = (user.full_name or user.first_name or "Путник").strip()
        self._db.upsert_user(user.id, user.username, name)
        # подтягиваем имя усадеб под username / полное имя (без дублей "Артём")
        self._db.set_fief_names_for_user(user.id, fief_name_for_user(user))

    def starter_tile_choices(self, realm_id: int, count: int = 3) -> list[dict]:
        """Предлагает стартовые клетки, максимально разнесённые на торе."""
        realm = self._db.get_realm(realm_id)
        width, height = int(realm["width"]), int(realm["height"])
        tiles = self._db.get_tiles(realm_id)
        # якоря - ядра существующих усадеб (иначе все занятые клетки)
        cores = [(t["x"], t["y"]) for t in tiles if t.get("is_core") and t["owner_fief_id"]]
        if not cores:
            cores = [(t["x"], t["y"]) for t in tiles if t["owner_fief_id"]]

        ruins = [
            (int(t["x"]), int(t["y"]))
            for t in tiles
            if t["tile_type"] == B.TILE_RUINS
        ]
        blocked = (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER, B.TILE_RUINS)
        candidates = [
            t
            for t in tiles
            if t["owner_fief_id"] is None
            and t["tile_type"] not in blocked
            and not t.get("is_overgrown")
            and not too_close_to_ruins(
                int(t["x"]), int(t["y"]), ruins, width, height
            )
        ]
        return pick_max_separated_tiles(candidates, cores, width, height, count)

    def has_fief_elsewhere(self, user_id: int, realm_id: int) -> bool:
        """У игрока уже есть усадьба в том же мире (в другой долине)."""
        realm = self._db.get_realm(realm_id) or {}
        world_id = realm.get("world_id")
        if world_id is None:
            return False
        owned = self._db.get_fief_by_user_world(user_id, int(world_id))
        return owned is not None and int(owned["realm_id"]) != int(realm_id)

    def join_fief(
        self,
        realm_id: int,
        user,
        tile_id: int,
    ) -> tuple[dict, str]:
        self._engine.ensure_user(user)
        existing = self._db.get_fief_by_user(realm_id, user.id)
        if existing:
            raise ValueError("У вас уже есть усадьба в этой долине")
        realm = self._db.get_realm(realm_id)
        if not realm or realm.get("world_id") is None:
            raise ValueError("Долина не привязана к континенту")
        owned = self._db.get_fief_by_user_world(user.id, int(realm["world_id"]))
        if owned:
            raise ValueError(second_fief_on_world_message())

        tile = self._db.get_tile_by_id(tile_id, realm_id)
        if not tile or tile["owner_fief_id"] is not None:
            raise ValueError("Клетка недоступна")
        if tile["tile_type"] in (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER, B.TILE_RUINS):
            raise ValueError("Нельзя начать здесь")

        width, height = int(realm["width"]), int(realm["height"])
        tiles = self._db.get_tiles(realm_id)
        ruins = [
            (int(t["x"]), int(t["y"]))
            for t in tiles
            if t["tile_type"] == B.TILE_RUINS
        ]
        if too_close_to_ruins(int(tile["x"]), int(tile["y"]), ruins, width, height):
            raise ValueError("Нельзя начать здесь")

        name = fief_name_for_user(user)
        with self._db.transaction():
            fief = self._db.create_fief(
                realm_id,
                user.id,
                name,
                grain=B.STARTING_GRAIN,
                goods=B.STARTING_GOODS,
                might=B.STARTING_MIGHT,
                actions=1,
                # выбор стартовой клетки уже выполнен - сразу квест на расширение
                onboard_step=2,
            )
            claimed = self._db.claim_unowned_tile(
                int(tile["id"]),
                int(realm_id),
                owner_fief_id=fief["id"],
                building=B.BLD_MANOR,
                building_level=B.STARTING_MANOR_LEVEL,
                is_core=True,
            )
            if claimed is None:
                raise ValueError("Клетка недоступна")
            self._db.set_last_realm(user.id, realm_id)
        self._engine.maybe_grow_map(realm_id)
        return fief, (
            f"🏡 {name} основана на {coord_label(tile['x'], tile['y'])} "
            f"({B.TILE_NAMES_RU[tile['tile_type']]}).\n"
            f"Стартовый набор: двор (главная клетка), {B.STARTING_GRAIN} зерна, "
            f"{B.STARTING_GOODS} товаров, {B.STARTING_MIGHT} силы.\n"
            f"Урожай собирается сам. Первый квест - занять соседнюю клетку "
            f"(от {B.CLAIM_COSTS[2]} товаров)."
        )
