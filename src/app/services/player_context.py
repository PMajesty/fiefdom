"""Контекст игрока: выбор долины/усадьбы и last_realm."""
from __future__ import annotations

from app.repos import PlayerContextRepos

from typing import Any


class PlayerContextService:
    def __init__(self, engine, db: PlayerContextRepos) -> None:
        self._engine = engine
        self._db = db

    def resolve_realm_for_user(
        self, user_id: int, chat: Any = None
    ) -> dict | None:
        """Realm из группового чата, last_realm с усадьбой пользователя или единственной усадьбы."""
        if chat is not None:
            chat_type = getattr(chat, "type", None)
            chat_id = getattr(chat, "id", None)
            if chat_type in ("group", "supergroup") and chat_id is not None:
                return self._db.get_realm_by_chat(chat_id)

        user = self._db.get_user(user_id)
        last_realm_id = user.get("last_realm_id") if user else None
        if last_realm_id:
            realm = self._db.get_realm(last_realm_id)
            if realm and self._db.get_fief_by_user(int(realm["id"]), user_id):
                return realm

        fiefs = self._db.list_fiefs_by_user(user_id)
        if len(fiefs) == 1:
            owned_realm_id = int(fiefs[0]["realm_id"])
            if last_realm_id is not None and int(last_realm_id) != owned_realm_id:
                self._db.set_last_realm(user_id, owned_realm_id)
            return self._db.get_realm(owned_realm_id)
        return None

    def resolve_fief_for_user(
        self,
        user_id: int,
        realm_id: int | None = None,
    ) -> dict | None:
        if realm_id is not None:
            return self._db.get_fief_by_user(realm_id, user_id)
        realm = self.resolve_realm_for_user(user_id)
        if realm:
            fief = self._db.get_fief_by_user(realm["id"], user_id)
            if fief:
                return fief
        fiefs = self._db.list_fiefs_by_user(user_id)
        if len(fiefs) == 1:
            return fiefs[0]
        return None

    def remember_last_realm(self, user_id: int, realm_id: int) -> None:
        self._db.set_last_realm(user_id, realm_id)

    def realm_by_chat(self, chat_id: int) -> dict | None:
        return self._db.get_realm_by_chat(chat_id)

    def fief_of_user_in_realm(self, user_id: int, realm_id: int) -> dict | None:
        return self._db.get_fief_by_user(realm_id, user_id)

    def fief_of_user_in_world(self, user_id: int, world_id: int) -> dict | None:
        return self._db.get_fief_by_user_world(user_id, world_id)

    def fiefs_of_user(self, user_id: int) -> list[dict]:
        return self._db.list_fiefs_by_user(user_id)

    def fief_by_id(self, fief_id: int) -> dict | None:
        return self._db.get_fief(fief_id)

    def require_owned_fief(self, fief_id: int, user_id: int) -> dict:
        fief = self._db.get_fief(fief_id)
        if not fief or fief["user_id"] != user_id:
            raise ValueError("Это не ваша усадьба")
        return fief

    def require_owned_active_fief(self, fief_id: int, user_id: int) -> dict:
        fief = self.require_owned_fief(fief_id, user_id)
        if not self._engine.fief_is_active_play(fief):
            raise ValueError(
                "Сначала выберите эту долину активной "
                "(откройте усадьбу здесь или список в /start)"
            )
        return fief
