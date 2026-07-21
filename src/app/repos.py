"""Узкие репозиторные Protocol'ы (ISP) над Database.

Database реализует все протоколы без изменения тел методов.
Сервисы зависят от составных Port'ов из нужных репозиториев, а не от всего Database.

Стрэддлеры (задокументированы у методов):
- WorldRepo.sync_realms_clock_from_world зеркалит часы на realms.
- RealmRepo.update_realm несёт rumor/digest/wipe JSONB.
- PactRepo.create_pact / dissolve_pact трогают fiefs.
- RaidLogRepo.log_raid и FiefRepo.create_fief зависят от resource registry.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class UnitOfWork(Protocol):
    def transaction(self) -> Iterator[None]: ...


@runtime_checkable
class WorldRepo(Protocol):
    def get_or_create_world(self) -> dict: ...

    def create_instance_world(
        self,
        *,
        name: str,
        parent_world_id: int,
        expires_tick: int | None = None,
        timezone: str = "Europe/Moscow",
    ) -> dict: ...

    def get_world(self, world_id: int | None = None) -> dict | None: ...

    def update_world(self, world_id: int, **fields: Any) -> None: ...

    def sync_realms_clock_from_world(self, world_id: int) -> None:
        """Стрэддлер: пишет в realms с clock_mode=shared."""
        ...


@runtime_checkable
class EarlyTickVoteStore(Protocol):
    def list_early_tick_votes(self, world_id: int) -> list[int]: ...

    def add_early_tick_vote(self, world_id: int, user_id: int) -> bool: ...

    def remove_early_tick_vote(self, world_id: int, user_id: int) -> bool: ...

    def clear_early_tick_votes(self, world_id: int) -> None: ...


@runtime_checkable
class RealmRepo(Protocol):
    def list_realms_by_chain(self, world_id: int) -> list[dict]: ...

    def shift_chain_indices(
        self, world_id: int, from_index: int, delta: int = 1
    ) -> None: ...

    def recompact_chain_indices(self, world_id: int) -> None: ...

    def lock_world_realms_for_links(self, world_id: int) -> None: ...

    def list_realm_link_degrees(self, world_id: int) -> dict[int, int]: ...

    def ensure_realm_link(self, realm_a: int, realm_b: int) -> None: ...

    def list_realm_neighbor_ids(self, realm_id: int) -> list[int]: ...

    def list_adjacent_realms(self, realm_id: int) -> list[dict]: ...

    def realms_are_adjacent(self, realm_a: int, realm_b: int) -> bool: ...

    def get_realm_by_chat(self, chat_id: int) -> dict | None: ...

    def get_realm(self, realm_id: int) -> dict | None: ...

    def list_realms(self) -> list[dict]: ...

    def create_realm(
        self,
        chat_id: int,
        title: str,
        width: int,
        height: int,
        timezone: str,
        tick_hour: int,
        tick_minute: int,
        feature_flags: dict,
        next_catastrophe_tick: int,
        *,
        world_id: int | None = None,
        chain_index: int | None = None,
        day_number: int = 1,
        tick_index: int = 0,
        last_tick_local_date=None,
        last_tick_slot: int | None = None,
        next_catastrophe_key: str | None = None,
        pending_minor_key: str | None = None,
        active_minor_key: str | None = None,
        clock_mode: str = "shared",
        realm_kind: str = "valley",
        expires_tick: int | None = None,
    ) -> dict: ...

    def update_realm(self, realm_id: int, **fields: Any) -> None:
        """Стрэддлер: rumor_queue / pending_raid_lines / wipe JSONB."""
        ...

    def delete_realm(self, realm_id: int) -> None: ...


@runtime_checkable
class UserRepo(Protocol):
    def upsert_user(
        self, telegram_id: int, username: str | None, display_name: str
    ) -> None: ...

    def set_last_realm(self, user_id: int, realm_id: int) -> None: ...

    def get_user(self, telegram_id: int) -> dict | None: ...


@runtime_checkable
class FiefRepo(Protocol):
    """Усадьбы, клетки и ресурсный ledger."""

    def insert_tiles(self, realm_id: int, tiles: list[dict]) -> None: ...

    def get_tiles(self, realm_id: int) -> list[dict]: ...

    def get_tile(self, realm_id: int, x: int, y: int) -> dict | None: ...

    def get_tile_by_id(self, tile_id: int, realm_id: int) -> dict | None: ...

    def update_tile(self, tile_id: int, **fields: Any) -> None: ...

    def claim_unowned_tile(
        self, tile_id: int, realm_id: int, **fields: Any
    ) -> dict | None: ...

    def fief_tiles(self, fief_id: int) -> list[dict]: ...

    def create_fief(
        self, realm_id: int, user_id: int, name: str, **resources: Any
    ) -> dict:
        """Стрэддлер: колонки из resource registry."""
        ...

    def get_fief(self, fief_id: int) -> dict | None: ...

    def get_fief_by_user(self, realm_id: int, user_id: int) -> dict | None: ...

    def get_fief_by_user_world(
        self, user_id: int, world_id: int
    ) -> dict | None: ...

    def list_fiefs(self, realm_id: int) -> list[dict]: ...

    def list_fiefs_by_user(self, user_id: int) -> list[dict]: ...

    def list_fiefs_by_world(self, world_id: int) -> list[dict]: ...

    def update_fief(self, fief_id: int, **fields: Any) -> None: ...

    def spend_fief_action(
        self,
        fief_id: int,
        *,
        last_active_at: datetime,
        last_active_tick: int,
    ) -> dict | None: ...

    def debit_fief_resources(
        self,
        fief_id: int,
        amounts: Mapping[str, int] | None = None,
        **kwargs: int,
    ) -> dict | None: ...

    def credit_fief_resources(
        self,
        fief_id: int,
        amounts: Mapping[str, int] | None = None,
        **kwargs: int,
    ) -> dict | None: ...

    def credit_campaign_return_might(
        self, fief_id: int, might: int
    ) -> dict | None: ...

    def set_fief_names_for_user(self, user_id: int, name: str) -> None: ...

    def touch_fief(self, fief_id: int) -> None: ...


@runtime_checkable
class PactRepo(Protocol):
    def create_pact(
        self, realm_id: int, name: str, founder_fief_id: int
    ) -> dict:
        """Стрэддлер: выставляет pact_id основателю в fiefs."""
        ...

    def get_pact(self, pact_id: int) -> dict | None: ...

    def update_pact(self, pact_id: int, **fields: Any) -> None: ...

    def pact_members(self, pact_id: int) -> list[dict]: ...

    def dissolve_pact(self, pact_id: int) -> None:
        """Стрэддлер: чистит invites и pact_id у fiefs."""
        ...

    def create_pact_invite(self, **fields: Any) -> dict: ...

    def get_pact_invite(self, invite_id: int) -> dict | None: ...

    def get_open_pact_invite(
        self, pact_id: int, target_fief_id: int
    ) -> dict | None: ...

    def claim_open_pact_invite(
        self, invite_id: int, new_status: str
    ) -> dict | None: ...

    def update_pact_invite(self, invite_id: int, **fields: Any) -> None: ...


@runtime_checkable
class TradeRepo(Protocol):
    def create_trade(self, **fields: Any) -> dict: ...

    def list_open_trades(
        self, realm_id: int, for_fief_id: int | None = None
    ) -> list[dict]: ...

    def list_expired_open_trades(
        self, realm_id: int, tick_index: int
    ) -> list[dict]: ...

    def get_trade(self, trade_id: int) -> dict | None: ...

    def claim_open_trade(self, trade_id: int) -> dict | None: ...

    def claim_cancel_open_trade(self, trade_id: int) -> dict | None: ...

    def update_trade(self, trade_id: int, **fields: Any) -> None: ...


@runtime_checkable
class RaidLogRepo(Protocol):
    def log_raid(self, **fields: Any) -> dict:
        """Стрэддлер: stolen-колонки из resource registry."""
        ...

    def count_raids_between(
        self, attacker_id: int, victim_id: int, since_tick: int
    ) -> int: ...

    def last_raid_attacker_victim(
        self, attacker_id: int, victim_id: int
    ) -> int | None: ...

    def raids_since_tick(self, realm_id: int, since_tick: int) -> list[dict]: ...


@runtime_checkable
class EventRepo(Protocol):
    def create_event(self, **fields: Any) -> dict: ...

    def get_active_events(
        self, realm_id: int, kind: str | None = None
    ) -> list[dict]: ...

    def update_event(self, event_id: int, **fields: Any) -> None: ...

    def get_event(self, event_id: int) -> dict | None: ...

    def bump_event_action(
        self,
        event_id: int,
        fief_id: int,
        action_key: str,
        amount: int,
    ) -> None: ...

    def event_actions(self, event_id: int) -> list[dict]: ...


@runtime_checkable
class TileEntityRepo(Protocol):
    def create_tile_entity(self, **fields: Any) -> dict: ...

    def list_active_tile_entities(self, realm_id: int) -> list[dict]: ...

    def list_tile_entities_at(
        self,
        realm_id: int,
        x: int,
        y: int,
        *,
        active_only: bool = True,
    ) -> list[dict]: ...

    def update_tile_entity(self, entity_id: int, **fields: Any) -> None: ...

    def claim_expire_tile_entity(self, entity_id: int) -> dict | None: ...

    def delete_tile_entity(self, entity_id: int) -> None: ...


@runtime_checkable
class ActionIntentRepo(Protocol):
    """Заявки declare-then-resolve, включая caravan-facet."""

    def get_action_intent(self, intent_id: int) -> dict | None: ...

    def create_action_intent(self, **fields: Any) -> dict: ...

    def list_open_action_intents(
        self, world_id: int, tick_index: int
    ) -> list[dict]: ...

    def list_raid_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]: ...

    def list_open_raid_intents_for_fief(self, fief_id: int) -> list[dict]: ...

    def list_caravan_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]: ...

    def list_road_caravan_intents_for_fief(self, fief_id: int) -> list[dict]: ...

    def list_recent_caravan_receiver_ids(
        self, fief_id: int, *, limit: int = 8
    ) -> list[int]: ...

    def list_cover_stance_intents(
        self,
        world_id: int,
        tick_index: int,
        *,
        statuses: tuple[str, ...] = ("open", "locked"),
    ) -> list[dict]: ...

    def list_open_cover_stance_intents_for_fief(self, fief_id: int) -> list[dict]: ...

    def lock_action_intents(
        self, world_id: int, tick_index: int, *, kind: str = "raid"
    ) -> int: ...

    def claim_resolve_action_intent(self, intent_id: int) -> dict | None: ...

    def cancel_action_intent(
        self,
        intent_id: int,
        *,
        statuses: tuple[str, ...] = ("open",),
    ) -> dict | None: ...

    def update_action_intent_payload(
        self, intent_id: int, payload: dict
    ) -> None: ...

    def update_open_action_intent_payload(
        self, intent_id: int, payload: dict
    ) -> dict | None: ...

    def mark_caravan_lock_announced(
        self,
        intent_ids: list[int] | tuple[int, ...],
        *,
        public_ids: list[int] | tuple[int, ...] = (),
    ) -> int: ...

    def mark_caravan_route_public(
        self, intent_ids: list[int] | tuple[int, ...]
    ) -> int: ...


@runtime_checkable
class PatchAnnounceRepo(Protocol):
    def list_announced_patch_names(self) -> set[str]: ...

    def mark_patch_announced(self, name: str) -> None: ...


@runtime_checkable
class DecreeRepo(Protocol):
    def next_decree_number(self, realm_id: int | None) -> int: ...

    def add_decree(self, realm_id: int | None, number: int, body: str) -> dict: ...


# --- составные Port'ы сервисов (только нужные репозитории) ---


@runtime_checkable
class CaravanRepos(
    WorldRepo,
    FiefRepo,
    RealmRepo,
    UserRepo,
    ActionIntentRepo,
    UnitOfWork,
    Protocol,
):
    """Persistence surface для CaravanService."""

    def pact_members(self, pact_id: int) -> list[dict]: ...


@runtime_checkable
class PactRepos(
    PactRepo, FiefRepo, RealmRepo, UnitOfWork, Protocol
):
    """Persistence surface для PactService."""


@runtime_checkable
class LandActionRepos(
    FiefRepo, RealmRepo, UnitOfWork, Protocol
):
    """Persistence surface для LandActionService."""


@runtime_checkable
class RaidDeclareRepos(
    FiefRepo,
    RealmRepo,
    WorldRepo,
    RaidLogRepo,
    ActionIntentRepo,
    UnitOfWork,
    Protocol,
):
    """Persistence surface для RaidDeclareService."""


@runtime_checkable
class NightRaidRepos(
    FiefRepo,
    RealmRepo,
    PactRepo,
    RaidLogRepo,
    ActionIntentRepo,
    UnitOfWork,
    Protocol,
):
    """Persistence surface для NightRaidResolver."""


@runtime_checkable
class CoverStanceRepos(
    FiefRepo,
    RealmRepo,
    WorldRepo,
    PactRepo,
    ActionIntentRepo,
    UnitOfWork,
    Protocol,
):
    """Persistence surface для CoverStanceService."""


@runtime_checkable
class OnboardingRepos(
    UserRepo, RealmRepo, FiefRepo, UnitOfWork, Protocol
):
    """Persistence surface для OnboardingService."""


@runtime_checkable
class RumorRepos(WorldRepo, RealmRepo, FiefRepo, Protocol):
    """Persistence surface для RumorService."""


@runtime_checkable
class RealmLifecycleRepos(
    WorldRepo, RealmRepo, FiefRepo, DecreeRepo, UnitOfWork, Protocol
):
    """Persistence surface для RealmLifecycleService."""


@runtime_checkable
class CatastropheRepos(
    WorldRepo, RealmRepo, FiefRepo, EventRepo, UnitOfWork, Protocol
):
    """Persistence surface для CatastropheService."""


@runtime_checkable
class PlayerContextRepos(UserRepo, RealmRepo, FiefRepo, Protocol):
    """Persistence surface для PlayerContextService."""


@runtime_checkable
class PatchAnnounceRepos(PatchAnnounceRepo, RealmRepo, Protocol):
    """Persistence surface для PatchAnnounceService."""


@runtime_checkable
class WorldTickRepos(WorldRepo, RealmRepo, UnitOfWork, Protocol):
    """Persistence surface для WorldTickOrchestrator."""


@runtime_checkable
class EarlyTickVoteRepos(
    WorldRepo,
    RealmRepo,
    FiefRepo,
    EarlyTickVoteStore,
    UnitOfWork,
    Protocol,
):
    """Persistence surface для EarlyTickVoteService."""


@runtime_checkable
class RealmTickRepos(
    RealmRepo,
    FiefRepo,
    EventRepo,
    TileEntityRepo,
    RaidLogRepo,
    Protocol,
):
    """Persistence surface для RealmTickRunner."""
