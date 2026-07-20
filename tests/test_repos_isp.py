"""ISP: Database реализует узкие репозитории; сервисы принимают Port-фейки."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app import balance as B
from app.database import Database
from app.domain.tick_pipeline import TICK_PHASE_ECONOMY
from app.repos import (
    ActionIntentRepo,
    DecreeRepo,
    EventRepo,
    FiefRepo,
    PactRepo,
    PatchAnnounceRepo,
    RaidLogRepo,
    RealmRepo,
    TileEntityRepo,
    TradeRepo,
    UnitOfWork,
    UserRepo,
    WorldRepo,
)
from app.services.land_actions import LandActionService
from app.services.night_raids import NightRaidResolver
from app.services.patch_announce import PatchAnnounceService
from app.services.world_tick import WorldTickOrchestrator


ENTITY_REPOS = (
    WorldRepo,
    RealmRepo,
    UserRepo,
    FiefRepo,
    PactRepo,
    TradeRepo,
    RaidLogRepo,
    EventRepo,
    TileEntityRepo,
    ActionIntentRepo,
    PatchAnnounceRepo,
    DecreeRepo,
    UnitOfWork,
)


def test_database_implements_all_entity_repos():
    db = Database.__new__(Database)
    for proto in ENTITY_REPOS:
        assert isinstance(db, proto), proto.__name__


def test_patch_announce_accepts_narrow_fake():
    announced: set[str] = set()

    class FakePatchDb:
        def list_announced_patch_names(self) -> set[str]:
            return {"notes-1"}

        def list_realms(self) -> list[dict]:
            return [{"id": 7, "title": "Долина"}]

        def mark_patch_announced(self, name: str) -> None:
            announced.add(name)

    engine = SimpleNamespace(db=MagicMock())
    svc = PatchAnnounceService(engine, FakePatchDb())
    assert svc.announced_names() == {"notes-1"}
    assert svc.realms_to_announce() == [{"id": 7, "title": "Долина"}]
    svc.mark_announced("notes-2")
    assert announced == {"notes-2"}


def test_world_tick_accepts_narrow_fake():
    """WorldTickPort surface: только update_world для _enter_tick_economy."""
    updates: list[tuple[int, dict]] = []

    class FakeWorldTickDb:
        def update_world(self, world_id: int, **fields) -> None:
            updates.append((world_id, fields))

    world = {"tick_phase": "play"}
    engine = SimpleNamespace(db=MagicMock())
    svc = WorldTickOrchestrator(engine, FakeWorldTickDb())
    svc._enter_tick_economy(3, world)
    assert world["tick_phase"] == TICK_PHASE_ECONOMY
    assert updates and updates[0][0] == 3
    assert updates[0][1].get("tick_phase") == TICK_PHASE_ECONOMY


def test_night_raid_accepts_narrow_fake():
    """NightRaidPort surface: pact_members для interceptor pick."""

    class FakeNightRaidDb:
        def pact_members(self, pact_id: int) -> list[dict]:
            assert pact_id == 11
            return [
                {
                    "id": 1,
                    "realm_id": 5,
                    "cover_allies": True,
                    "might": 100,
                },
                {
                    "id": 2,
                    "realm_id": 5,
                    "cover_allies": True,
                    "might": 50,
                },
            ]

    engine = SimpleNamespace(db=MagicMock())
    svc = NightRaidResolver(engine, FakeNightRaidDb())
    vic = {"id": 2, "pact_id": 11, "realm_id": 5}
    picked = svc._pick_raid_interceptor(vic, incomplete_world=False)
    assert picked is not None
    assert picked["id"] == 1
    assert int(picked["might"]) >= B.INTERCEPT_MIGHT


def test_land_action_accepts_narrow_fake():
    """LandActionPort surface: fief_tiles для demolish_options."""

    class FakeLandActionDb:
        def fief_tiles(self, fief_id: int) -> list[dict]:
            assert fief_id == 9
            return [
                {"id": 1, "is_overgrown": False, "x": 0, "y": 0},
                {"id": 2, "is_overgrown": True, "x": 1, "y": 0},
            ]

    engine = SimpleNamespace(db=MagicMock())
    svc = LandActionService(engine, FakeLandActionDb())
    opts = svc.demolish_options(9)
    assert opts == [{"id": 1, "is_overgrown": False, "x": 0, "y": 0}]
