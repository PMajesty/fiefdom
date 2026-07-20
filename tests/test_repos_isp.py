"""ISP: Database реализует узкие репозитории; сервисы принимают Port-фейки."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.database import Database
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
from app.services.patch_announce import PatchAnnounceService


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
