"""Патч-вестники: учёт объявленных имён и список долин для рассылки."""
from __future__ import annotations

from app.repos import PatchAnnounceRepos


class PatchAnnounceService:
    def __init__(self, engine, db: PatchAnnounceRepos) -> None:
        self._engine = engine
        self._db = db

    def announced_names(self) -> set[str]:
        return self._db.list_announced_patch_names()

    def realms_to_announce(self) -> list[dict]:
        return self._db.list_realms()

    def mark_announced(self, name: str) -> None:
        self._db.mark_patch_announced(name)
