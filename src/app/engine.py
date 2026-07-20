"""Игровой движок: операции над долиной через БД + доменную логику."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from app import balance as B
from app.config import TIMEZONE, tick_slots
from app.database import Database
from app.domain import absence as absence_mod
from app.domain.digest import format_decree
from app.domain.economy import (
    TileView,
    adjacent_claimable,
    fief_daily_production,
    render_map_parts,
)
from app.domain.holdings import format_holdings
from app.domain.map_image import (
    MapImageCache,
    MapPhoto,
    map_fingerprint,
    render_map_image,
)
from app.domain.modifiers import (
    LIVE_READ_MODIFIER_KINDS,
    ActiveCatastropheRef,
    ModifierSet,
    RealmModifierCtx,
    collect_active_modifiers,
)
from app.domain.tile_entities import (
    ActiveTileEntityRef,
    active_tile_entity_ref,
    entity_fingerprint_rows,
    entity_map_marks,
)

# Kinds, которые Engine читает на live-путях (farm/fog/trade/build/wedding).
ENGINE_CONSUMED_MODIFIER_KINDS = LIVE_READ_MODIFIER_KINDS
from app.domain.ticks import tick_active
from app.domain.map_gen import GenTile, append_strip
from app.domain.caravans import (
    DeclareCaravanResult,
    ResolveCaravanReport,
)
from app.domain.raids import (
    DeclareRaidResult,
    RaidNightPartyNotice,
    ResolveNightReport,
    standing_raid_defense,
)
from app.domain.rumors import (
    FiefRumorSnapshot,
    UpcomingEventHint,
)
from app.domain.resources import (
    fief_balance_columns,
    format_daily_production_line,
    format_status_stash_line,
    pending_from_row,
    resource_name_ru,
    stash_from_row,
)
from app.domain.tick import (
    collect_pending_bags,
)
from app.presenters.intents import (
    PreparedCaravanView,
    PreparedRaidView,
    render_prepared_intent_status_lines,
    render_prepared_intents_card,
)
from app.presenters.map import compose_map_photo, render_map_text
from app.presenters.status import StatusSnapshot, render_status_card
from app.services.caravans import CaravanService
from app.services.land_actions import (
    LandActionService,
    try_complete_onboard_build,
    try_complete_onboard_claim,
)
from app.services.pacts import PactService
from app.services.night_raids import NightRaidResolver
from app.domain.tick_pipeline import (
    ActionWindow,
    normalize_tick_phase,
    TICK_PHASE_PLAY,
)
from app.domain.tick_schedule import (
    format_next_tick_line,
    next_tick_datetime,
    play_window_bounds,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None


from app.services.world_tick import WorldTickOrchestrator
from app.services.realm_tick import RealmTickRunner


def fief_name_for_user(user) -> str:
    """Уникальное читаемое имя усадьбы: @username, иначе полное имя.

    Принимает Telegram User / SimpleNamespace или dict из таблицы users.
    """
    if isinstance(user, dict):
        username = (user.get("username") or "").strip()
        display = (
            user.get("display_name")
            or user.get("full_name")
            or user.get("first_name")
            or "Путник"
        )
    else:
        username = (getattr(user, "username", None) or "").strip()
        display = (
            getattr(user, "full_name", None)
            or getattr(user, "first_name", None)
            or "Путник"
        )
    if username:
        label = f"@{username}"
    else:
        label = str(display).strip() or "Путник"
    return f"Усадьба {label}"[:40]


from app.services.onboarding import OnboardingService

def _stash_status_line(barn_level: int) -> str:
    cap = B.stash_cap(barn_level)
    if barn_level <= 0:
        return f"Склад до {cap} · без амбара"
    roman = {1: "I", 2: "II", 3: "III"}.get(barn_level, str(barn_level))
    return f"Склад до {cap} · амбар {roman}"


def onboard_quest_html(onboard_step: int) -> str | None:
    """Громкая строка квеста для статус-карточки (шаги 2 и 3)."""
    step = int(onboard_step)
    if step == 2:
        return (
            f"<b>Квест: займите соседнюю клетку "
            f"(+{B.ONBOARD_DAY2_GOODS} товаров).</b>"
        )
    if step == 3:
        return (
            f"<b>Квест: постройте или улучшите здание "
            f"(+{B.ONBOARD_DAY3_GOODS} товаров).</b>"
        )
    return None


def onboard_patience_hint(
    *,
    onboard_step: int,
    goods: int,
    tile_count: int,
    min_build_cost: int | None,
) -> str | None:
    """Тихая подсказка, если текущий квест пока не по карману."""
    step = int(onboard_step)
    goods = int(goods)
    next_claim = None
    if tile_count < B.TILE_HARD_CAP:
        try:
            next_claim = B.claim_cost(int(tile_count) + 1)
        except ValueError:
            next_claim = None
    can_claim = next_claim is not None and goods >= next_claim
    can_build = min_build_cost is not None and goods >= int(min_build_cost)
    if step == 2:
        if can_claim:
            return None
        claim_s = str(next_claim) if next_claim is not None else "-"
        return (
            f"Пока копите товары или зайдите на рынок: земля от {claim_s}."
        )
    if step == 3:
        if can_build:
            return None
        build_s = str(min_build_cost) if min_build_cost is not None else "-"
        return (
            f"Пока копите товары или зайдите на рынок: стройка от {build_s}."
        )
    return None

def raid_pact_unlocked(*, onboard_step: int, day_number: int) -> bool:
    """Набег/Пакт в UI: квесты закрыты (onboard_step >= 4) и день долины >= RAID_PACT_UNLOCK_DAY."""
    return int(onboard_step) >= 4 and int(day_number) >= int(B.RAID_PACT_UNLOCK_DAY)


def raid_pact_lock_hint(*, onboard_step: int, day_number: int) -> str | None:
    """Короткий хвост для подписи кнопки (\"после квестов\" / \"с дня N\"). None если открыто."""
    if raid_pact_unlocked(onboard_step=onboard_step, day_number=day_number):
        return None
    if int(onboard_step) < 4:
        return "после квестов"
    return f"с дня {int(B.RAID_PACT_UNLOCK_DAY)}"


def raid_pact_lock_message(*, onboard_step: int, day_number: int) -> str:
    """Пояснение по тапу на замок (без трат)."""
    hint = raid_pact_lock_hint(onboard_step=onboard_step, day_number=day_number)
    if hint is None:
        return "Набег и пакт уже доступны."
    if hint == "после квестов":
        return "Набег и пакт - после квестов."
    return f"Набег и пакт - {hint} долины."


from app.services.catastrophes import CatastropheService
from app.services.raid_declare import RaidDeclareService
from app.services.realm_admin import RealmLifecycleService
from app.services.rumors import RumorService

class Engine:
    def __init__(self, db: Database):
        self.db = db
        self._map_image_cache = MapImageCache()

    # ---------- realm ----------
    def create_realm(self, chat_id: int, title: str, creator_user_id: int) -> tuple[dict, str]:
        return RealmLifecycleService(self).create_realm(
            chat_id, title, creator_user_id
        )

    def begin_wipe(self, realm_id: int) -> str:
        return RealmLifecycleService(self).begin_wipe(realm_id)

    def confirm_wipe(self, realm_id: int, code: str, confirm_word: str) -> str:
        return RealmLifecycleService(self).confirm_wipe(
            realm_id, code, confirm_word
        )

    def list_realms_with_fief_counts(
        self,
    ) -> tuple[list[dict], dict[int, int]]:
        return RealmLifecycleService(self).list_realms_with_fief_counts()

    def get_realm(self, realm_id: int) -> dict | None:
        return RealmLifecycleService(self).get_realm(realm_id)

    def grant_resources(
        self,
        realm_id: int,
        fief_id: int,
        deltas: dict[str, int],
    ) -> None:
        return RealmLifecycleService(self).grant_resources(
            realm_id, fief_id, deltas
        )

    def set_fief_frozen(self, fief_id: int, frozen: bool) -> None:
        return RealmLifecycleService(self).set_fief_frozen(fief_id, frozen)

    def set_active_minor(self, realm_id: int, key: str) -> None:
        return RealmLifecycleService(self).set_active_minor(realm_id, key)

    def issue_decree(self, realm_id: int, body: str) -> int:
        return RealmLifecycleService(self).issue_decree(realm_id, body)

    def default_world(self) -> dict:
        return self.db.get_or_create_world()

    def world(self, world_id: int) -> dict | None:
        return self.db.get_world(world_id)

    def realms_of_world(self, world_id: int) -> list[dict]:
        return self.db.list_realms_by_chain(world_id)

    # ---------- join / onboarding ----------
    def ensure_user(self, user) -> None:
        return OnboardingService(self).ensure_user(user)

    def starter_tile_choices(self, realm_id: int, count: int = 3) -> list[dict]:
        return OnboardingService(self).starter_tile_choices(realm_id, count)

    def has_fief_elsewhere(self, user_id: int, realm_id: int) -> bool:
        return OnboardingService(self).has_fief_elsewhere(user_id, realm_id)

    def join_fief(
        self,
        realm_id: int,
        user,
        tile_id: int,
    ) -> tuple[dict, str]:
        return OnboardingService(self).join_fief(realm_id, user, tile_id)


    def fief_label(self, fief: dict | None) -> str:
        """Публичное имя из профиля владельца; при расхождении обновляет fiefs.name."""
        if not fief:
            return "Усадьба"
        user = self.db.get_user(int(fief["user_id"])) if fief.get("user_id") else None
        if not user:
            return str(fief.get("name") or "Усадьба")
        label = fief_name_for_user(user)
        if fief.get("id") is not None and label != fief.get("name"):
            self.db.update_fief(int(fief["id"]), name=label)
        return label


    # ---------- views ----------
    def tile_views(self, realm_id: int) -> list[TileView]:
        tiles = self.db.get_tiles(realm_id)
        return [
            TileView(
                x=t["x"],
                y=t["y"],
                tile_type=t["tile_type"],
                owner_fief_id=t["owner_fief_id"],
                building=t.get("building"),
                building_level=int(t.get("building_level") or 0),
                is_bridge=bool(t.get("is_bridge")),
                is_core=bool(t.get("is_core")),
                is_overgrown=bool(t.get("is_overgrown")),
            )
            for t in tiles
        ]

    def barn_level(self, fief_id: int) -> int:
        levels = [
            int(t["building_level"])
            for t in self.db.fief_tiles(fief_id)
            if t.get("building") == B.BLD_BARN and not t.get("is_overgrown")
        ]
        return max(levels) if levels else 0

    def fief_prod(self, fief: dict, farm_mult: float = 1.0) -> Any:
        views = [
            TileView(
                x=t["x"],
                y=t["y"],
                tile_type=t["tile_type"],
                owner_fief_id=t["owner_fief_id"],
                building=t.get("building"),
                building_level=int(t.get("building_level") or 0),
                is_core=bool(t.get("is_core")),
                is_overgrown=bool(t.get("is_overgrown")),
            )
            for t in self.db.fief_tiles(fief["id"])
        ]
        return fief_daily_production(
            views,
            hungry=bool(fief["hungry"]),
            farm_mult=farm_mult,
            current_might=int(fief.get("might") or 0),
        )

    def collect_for_fief(self, fief_id: int, *, include_might: bool = True) -> list[str]:
        fief = self.db.get_fief(fief_id)
        if not fief:
            return []
        barn = self.barn_level(fief_id)
        stash, pending, notes = collect_pending_bags(
            stash_from_row(fief),
            pending_from_row(fief),
            barn,
            include_might=include_might,
        )
        self.db.update_fief(
            fief_id,
            **fief_balance_columns(stash, pending),
            last_active_at=_utcnow(),
            last_active_tick=int(
                (self.db.get_realm(fief["realm_id"]) or {}).get("tick_index") or 0
            ),
        )
        return notes

    def status_snapshot(self, fief_id: int) -> StatusSnapshot:
        """Мутации + часы + defense; HTML собирает presenters.status."""
        notes = self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        # Старые усадьбы, застрявшие на шаге 1 до починки онбординга.
        if int(fief.get("onboard_step") or 0) == 1:
            self.db.update_fief(fief_id, onboard_step=2)
            fief = self.db.get_fief(fief_id)
        realm = self.db.get_realm(fief["realm_id"])
        tick_index = int(realm.get("tick_index") or 0)
        tiles = self.db.fief_tiles(fief_id)
        prod = self.fief_prod(fief)
        barn = self.barn_level(fief_id)
        flags = []
        if fief["hungry"]:
            flags.append("Голод")
        if tick_active(fief.get("patrol_until_tick"), tick_index):
            flags.append("Дозор")
        if tick_active(fief.get("shield_until_tick"), tick_index):
            flags.append("Щит")
        last_active_tick = fief.get("last_active_tick")
        inactive_ticks = (
            tick_index - int(last_active_tick)
            if last_active_tick is not None
            else B.OVERGROWN_TICKS
        )
        tier = absence_mod.inactivity_tier(inactive_ticks)
        if tier == "dormant":
            flags.append("Дремлет")
        militia = B.militia_upkeep_grain(fief["might"])
        land = B.land_upkeep(len([t for t in tiles if not t.get("is_overgrown")]))
        active_tiles = [t for t in tiles if not t.get("is_overgrown")]
        # Уже расширились, но квест на клейм ещё висит (старые усадьбы / сбой).
        if int(fief.get("onboard_step") or 0) == 2 and len(active_tiles) >= 2:
            self._onboard_claim(fief_id)
            fief = self.db.get_fief(fief_id)
        alerts: list[str] = []
        quest = onboard_quest_html(fief["onboard_step"])
        if quest:
            alerts.append(quest)
            patience = onboard_patience_hint(
                onboard_step=int(fief["onboard_step"]),
                goods=int(fief["goods"]),
                tile_count=len(active_tiles),
                min_build_cost=B.min_any_build_action_cost(active_tiles),
            )
            if patience:
                alerts.append(patience)
        else:
            hint = raid_pact_lock_hint(
                onboard_step=int(fief.get("onboard_step") or 0),
                day_number=int(realm["day_number"]),
            )
            if hint:
                alerts.append(f"Набег и пакт - {hint}.")
        if flags:
            alerts.append(f"Статусы: {', '.join(flags)}")
        defense = standing_raid_defense(
            watch_defense=prod.defense,
            victim_might=int(fief.get("might") or 0),
            patrol_active=tick_active(fief.get("patrol_until_tick"), tick_index),
            fog_ignores_patrol=self.realm_modifiers(realm).fog_ignores_patrol(),
        )
        tz_name = realm.get("timezone") or TIMEZONE
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo(TIMEZONE)
        local_now = datetime.now(tz)
        last_slot = realm.get("last_tick_slot")
        next_at = next_tick_datetime(
            local_now=local_now,
            last_tick_local_date=_as_date(realm.get("last_tick_local_date")),
            last_tick_slot=int(last_slot) if last_slot is not None else None,
            slots=tick_slots(),
        )
        return StatusSnapshot(
            fief_label=self.fief_label(fief),
            day_number=int(realm["day_number"]),
            alerts=tuple(alerts),
            actions=int(fief["actions"]),
            actions_max=int(B.ACTIONS_BANK_MAX),
            tile_count=len(tiles),
            tile_cap=int(B.TILE_HARD_CAP),
            stash_line=format_status_stash_line(fief, defense=defense),
            barn_line=_stash_status_line(barn),
            production_line=format_daily_production_line(prod.resources()),
            land_upkeep=int(land),
            militia_upkeep=int(militia),
            next_tick_line=format_next_tick_line(next_at, local_now=local_now),
            prep_lines=tuple(self._prepared_intent_status_lines(fief_id)),
            notes=tuple(notes),
        )

    def status_card(self, fief_id: int) -> str:
        return render_status_card(self.status_snapshot(fief_id))

    def list_prepared_intents(
        self, fief_id: int
    ) -> tuple[list[dict], list[dict]]:
        """Исходящие заявки усадьбы: (набеги open/locked, обозы open)."""
        raids_raw = self.db.list_open_raid_intents_for_fief(int(fief_id))
        caravans_raw = self.db.list_open_caravan_intents_for_fief(int(fief_id))
        raids = list(raids_raw) if raids_raw else []
        caravans = list(caravans_raw) if caravans_raw else []
        return raids, caravans

    def prepared_intents_count(self, fief_id: int) -> int:
        raids, caravans = self.list_prepared_intents(fief_id)
        return len(raids) + len(caravans)

    def raid_intent_target_label(self, intent: dict) -> str:
        payload = intent.get("payload") or {}
        vid = int(payload.get("victim_id") or 0)
        vic = self.db.get_fief(vid) if vid else None
        return self.fief_label(vic) if vic else "?"

    def caravan_intent_target_label(self, intent: dict) -> str:
        return CaravanService(self).caravan_intent_target_label(intent)

    def _prepared_intent_views(
        self, fief_id: int
    ) -> tuple[tuple[PreparedRaidView, ...], tuple[PreparedCaravanView, ...]]:
        raids_raw, caravans_raw = self.list_prepared_intents(fief_id)
        raids: list[PreparedRaidView] = []
        for intent in raids_raw:
            payload = intent.get("payload") or {}
            raids.append(
                PreparedRaidView(
                    target_label=self.raid_intent_target_label(intent),
                    might=int(payload.get("might") or 0),
                    is_open=intent.get("status") == "open",
                )
            )
        caravans: list[PreparedCaravanView] = []
        for intent in caravans_raw:
            payload = intent.get("payload") or {}
            res = str(payload.get("res") or "")
            caravans.append(
                PreparedCaravanView(
                    target_label=self.caravan_intent_target_label(intent),
                    amount=int(payload.get("amt") or 0),
                    resource_name=resource_name_ru(res) if res else "?",
                )
            )
        return tuple(raids), tuple(caravans)

    def _prepared_intent_status_lines(self, fief_id: int) -> list[str]:
        """Короткие строки для статус-карточки: открытые/закрытые набеги и обозы."""
        raids, caravans = self._prepared_intent_views(fief_id)
        return render_prepared_intent_status_lines(raids, caravans)

    def prepared_intents_card(self, fief_id: int) -> str:
        """Карточка управления исходящими набегами и обозами."""
        raids, caravans = self._prepared_intent_views(fief_id)
        return render_prepared_intents_card(raids, caravans)

    def holdings_text(self, fief_id: int) -> str:
        fief = self.db.get_fief(fief_id)
        if not fief:
            return "Усадьба не найдена."
        tiles = self.db.fief_tiles(fief_id)
        return format_holdings(
            tiles,
            fief_label=self.fief_label(fief),
            hungry=bool(fief.get("hungry")),
            daily=self.fief_prod(fief),
            current_might=int(fief.get("might") or 0),
        )

    def _map_view_context(
        self, realm_id: int, highlight_fief_id: int | None = None
    ) -> tuple[dict, list[TileView], dict[int, str], set[tuple[int, int]] | None]:
        realm = self.db.get_realm(realm_id)
        views = self.tile_views(realm_id)
        fiefs = {f["id"]: f for f in self.db.list_fiefs(realm_id)}
        legend: dict[int, str] = {}
        for fid, f in fiefs.items():
            tag = ""
            if f.get("pact_id"):
                p = self.db.get_pact(f["pact_id"])
                if p:
                    tag = f" [{p['name']}]"
            legend[fid] = f"{self.fief_label(f)}{tag}"
        claimable = None
        if highlight_fief_id:
            owned = {
                (t.x, t.y)
                for t in views
                if t.owner_fief_id == highlight_fief_id and not t.is_overgrown
            }
            claimable = adjacent_claimable(
                owned,
                {(t.x, t.y): t for t in views},
                width=realm["width"],
                height=realm["height"],
                for_fief_id=highlight_fief_id,
            )
        return realm, views, legend, claimable

    def map_text(self, realm_id: int, highlight_fief_id: int | None = None) -> str:
        realm, views, legend, claimable = self._map_view_context(
            realm_id, highlight_fief_id
        )
        grid, footer = render_map_parts(
            realm["width"],
            realm["height"],
            views,
            legend,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
        )
        return render_map_text(
            title=str(realm["title"]),
            day_number=int(realm["day_number"]),
            grid=grid,
            footer=footer,
        )

    def map_photo(self, realm_id: int, highlight_fief_id: int | None = None) -> MapPhoto:
        realm, views, legend, claimable = self._map_view_context(
            realm_id, highlight_fief_id
        )
        _, footer = render_map_parts(
            realm["width"],
            realm["height"],
            views,
            legend,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
        )
        entity_rows = self.db.list_active_tile_entities(int(realm["id"]))
        fp_entities = entity_fingerprint_rows(entity_rows)
        mark_entities = entity_map_marks(entity_rows)
        fingerprint = map_fingerprint(
            realm_id=int(realm["id"]),
            width=int(realm["width"]),
            height=int(realm["height"]),
            tiles=views,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
            entity_rows=fp_entities or None,
        )
        cached = self._map_image_cache.get(fingerprint)
        if cached is not None:
            return compose_map_photo(
                png_bytes=cached.png_bytes,
                title=str(realm["title"]),
                day_number=int(realm["day_number"]),
                footer=footer,
                fingerprint=fingerprint,
                file_id=cached.file_id,
            )
        png_bytes = render_map_image(
            int(realm["width"]),
            int(realm["height"]),
            views,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
            entity_marks=mark_entities or None,
        )
        self._map_image_cache.put_png(fingerprint, png_bytes)
        return compose_map_photo(
            png_bytes=png_bytes,
            title=str(realm["title"]),
            day_number=int(realm["day_number"]),
            footer=footer,
            fingerprint=fingerprint,
            file_id=None,
        )

    def remember_map_file_id(self, fingerprint: str, file_id: str) -> None:
        self._map_image_cache.set_file_id(fingerprint, file_id)

    # ---------- actions ----------
    def fief_is_active_play(self, fief: dict) -> bool:
        """True если усадьба в активной долине владельца (или единственная)."""
        user_id = int(fief["user_id"])
        user = self.db.get_user(user_id) or {}
        last = user.get("last_realm_id")
        owned = self.db.list_fiefs_by_user(user_id)
        if len(owned) <= 1:
            return True
        if last is None:
            return False
        return int(last) == int(fief["realm_id"])

    def require_active_fief(self, fief_id: int) -> dict:
        fief = self.db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        if not self.fief_is_active_play(fief):
            raise ValueError(
                "Сначала выберите эту долину активной "
                "(меню усадьбы / список долин в /start)"
            )
        return fief

    def _spend_action(self, fief: dict) -> dict:
        if fief["actions"] < 1:
            raise ValueError("Нет действий на сегодня (макс. запас 3)")
        if fief["frozen"]:
            raise ValueError("Усадьба заморожена")
        if not self.fief_is_active_play(fief):
            raise ValueError(
                "Сначала выберите эту долину активной "
                "(меню усадьбы / список долин в /start)"
            )
        self._require_action_window(int(fief["realm_id"]))
        realm = self.db.get_realm(fief["realm_id"]) or {}
        updated = self.db.spend_fief_action(
            int(fief["id"]),
            last_active_at=_utcnow(),
            last_active_tick=int(realm.get("tick_index") or 0),
        )
        if not updated:
            cur = self.db.get_fief(int(fief["id"]))
            if cur and cur.get("frozen"):
                raise ValueError("Усадьба заморожена")
            raise ValueError("Нет действий на сегодня (макс. запас 3)")
        fief.update(updated)
        return updated


    def claim_tile(self, fief_id: int, x: int, y: int) -> str:
        return LandActionService(self).claim_tile(fief_id, x, y)

    def build_or_upgrade(self, fief_id: int, x: int, y: int, building: str) -> str:
        return LandActionService(self).build_or_upgrade(fief_id, x, y, building)

    def demolish_building(self, fief_id: int, x: int, y: int) -> str:
        return LandActionService(self).demolish_building(fief_id, x, y)

    def gather_resource(self, fief_id: int, resource: str) -> str:
        return LandActionService(self).gather_resource(fief_id, resource)

    def _onboard_claim(self, fief_id: int) -> None:
        return LandActionService(self)._onboard_claim(fief_id)

    def _onboard_build(self, fief_id: int) -> None:
        return LandActionService(self)._onboard_build(fief_id)

    def patrol(self, fief_id: int) -> str:
        return LandActionService(self).patrol(fief_id)


    def contribute_catastrophe_might(
        self, event_id: int, user_id: int, amount: int = 5
    ) -> int:
        return CatastropheService(self).contribute_catastrophe_might(
            event_id, user_id, amount
        )


    def list_raid_target_fiefs(self, attacker_fief_id: int) -> list[dict]:
        return RaidDeclareService(self).list_raid_target_fiefs(attacker_fief_id)

    def _world_local_now(self, world: dict) -> datetime:
        try:
            tz = ZoneInfo(world.get("timezone") or TIMEZONE)
        except Exception:
            tz = ZoneInfo(TIMEZONE)
        return datetime.now(tz)

    def _as_aware_utc(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def ensure_play_opened_at(self, world_id: int) -> dict:
        """Для живых миров без метки: старт окна с сейчас (деплой mid-play)."""
        world = self.db.get_world(world_id) or {}
        if world.get("play_opened_at") is not None:
            return world
        if normalize_tick_phase(world.get("tick_phase")) != TICK_PHASE_PLAY:
            return world
        now = _utcnow()
        self.db.update_world(int(world_id), play_opened_at=now)
        world["play_opened_at"] = now
        return world

    def play_window_bounds_for_world(
        self, world: dict
    ) -> tuple[datetime, datetime] | None:
        world = self.ensure_play_opened_at(int(world["id"]))
        local_now = self._world_local_now(world)
        opened = self._as_aware_utc(world.get("play_opened_at"))
        if opened is None:
            return None
        opened_local = opened.astimezone(local_now.tzinfo)
        next_at = next_tick_datetime(
            local_now=local_now,
            last_tick_local_date=_as_date(world.get("last_tick_local_date")),
            last_tick_slot=(
                int(world["last_tick_slot"])
                if world.get("last_tick_slot") is not None
                else None
            ),
            slots=tick_slots(),
        )
        return play_window_bounds(opened_local, next_at)

    def raid_declare_is_open(self, world: dict) -> bool:
        return RaidDeclareService(self).raid_declare_is_open(world)

    def format_raid_deadline(self, world: dict, *, midpoint: bool) -> str:
        return RaidDeclareService(self).format_raid_deadline(
            world, midpoint=midpoint
        )

    def _format_raid_deadline(self, world: dict, *, midpoint: bool) -> str:
        return self.format_raid_deadline(world, midpoint=midpoint)

    def _refund_action(self, fief_id: int) -> None:
        return RaidDeclareService(self)._refund_action(fief_id)

    def _raid_declare_gates(
        self, attacker_id: int, victim_id: int, might: int
    ) -> tuple[dict, dict, dict, dict, int]:
        return RaidDeclareService(self)._raid_declare_gates(
            attacker_id, victim_id, might
        )

    def declare_raid(
        self,
        attacker_id: int,
        victim_id: int,
        might: int,
        *,
        open_truce: bool = False,
    ) -> DeclareRaidResult:
        return RaidDeclareService(self).declare_raid(
            attacker_id, victim_id, might, open_truce=open_truce
        )

    def cancel_raid_intent(self, fief_id: int, intent_id: int) -> str:
        return RaidDeclareService(self).cancel_raid_intent(fief_id, intent_id)

    def lock_open_raid_intents(self, world_id: int) -> int:
        world = self.db.get_world(world_id) or {}
        tick_index = int(world.get("tick_index") or 0)
        return self.db.lock_action_intents(int(world_id), tick_index, kind="raid")

    def maybe_lock_raids_at_midpoint(self, world_id: int) -> int:
        return RaidDeclareService(self).maybe_lock_raids_at_midpoint(world_id)


    def _append_pending_raid_line(self, realm_id: int, line: str) -> None:
        if not line:
            return
        r = self.db.get_realm(int(realm_id)) or {}
        lines = list(r.get("pending_raid_lines") or [])
        lines.append(line)
        self.db.update_realm(int(realm_id), pending_raid_lines=lines)


    def _pick_raid_interceptor(
        self, vic: dict, *, incomplete_world: bool
    ) -> dict | None:
        return NightRaidResolver(self)._pick_raid_interceptor(
            vic, incomplete_world=incomplete_world
        )

    def _siege_probe_would_succeed(
        self,
        *,
        attack_might: int,
        watch_def: float,
        patrol: bool,
        fog: bool,
        victim_might: int,
        intercept: bool,
    ) -> bool:
        return NightRaidResolver(self)._siege_probe_would_succeed(
            attack_might=attack_might,
            watch_def=watch_def,
            patrol=patrol,
            fog=fog,
            victim_might=victim_might,
            intercept=intercept,
        )

    def resolve_pending_raids(
        self, world_id: int, tick_index: int
    ) -> ResolveNightReport:
        return NightRaidResolver(self).resolve_pending_raids(world_id, tick_index)

    def _resolve_victim_night(
        self,
        *,
        world_id: int,
        tick_index: int,
        victim_id: int,
        intents: list[dict],
        report: ResolveNightReport,
    ) -> None:
        return NightRaidResolver(self)._resolve_victim_night(
            world_id=world_id,
            tick_index=tick_index,
            victim_id=victim_id,
            intents=intents,
            report=report,
        )



    # ---------- caravans ----------
    def declare_caravan(
        self,
        from_fief_id: int,
        to_fief_id: int,
        res: str,
        amt: int,
    ) -> DeclareCaravanResult:
        return CaravanService(self).declare_caravan(
            from_fief_id, to_fief_id, res, amt
        )

    def cancel_caravan_intent(self, fief_id: int, intent_id: int) -> str:
        return CaravanService(self).cancel_caravan_intent(fief_id, intent_id)

    def resolve_pending_caravans(
        self, world_id: int, tick_index: int
    ) -> ResolveCaravanReport:
        return CaravanService(self).resolve_pending_caravans(
            world_id, tick_index
        )



    # ---------- pacts ----------
    def create_pact(self, fief_id: int, name: str) -> str:
        return PactService(self).create_pact(fief_id, name)

    def invite_to_pact(self, founder_fief_id: int, target_fief_id: int) -> dict:
        return PactService(self).invite_to_pact(founder_fief_id, target_fief_id)

    def accept_pact_invite(self, target_fief_id: int, invite_id: int) -> str:
        return PactService(self).accept_pact_invite(target_fief_id, invite_id)

    def decline_pact_invite(self, actor_fief_id: int, invite_id: int) -> str:
        return PactService(self).decline_pact_invite(actor_fief_id, invite_id)

    def leave_pact(self, fief_id: int) -> str:
        return PactService(self).leave_pact(fief_id)

    def set_cover(self, fief_id: int, enabled: bool) -> str:
        return PactService(self).set_cover(fief_id, enabled)



    # ---------- map growth / absence ----------
    def maybe_grow_map(self, realm_id: int) -> str | None:
        realm = self.db.get_realm(realm_id)
        tiles = self.db.get_tiles(realm_id)
        fiefs = self.db.list_fiefs(realm_id)
        claimed = sum(1 for t in tiles if t["owner_fief_id"] and not t.get("is_overgrown"))
        total = len(tiles)
        need = B.map_target_tiles(max(1, len(fiefs)))
        grow = False
        if total < B.MAP_MAX_TILES and claimed / max(1, total) >= B.MAP_GROWTH_CLAIMED_RATIO:
            grow = True
        if total < need and total < B.MAP_MAX_TILES:
            grow = True
        if not grow:
            return None
        axis = "row" if realm["width"] >= realm["height"] else "col"
        existing = [
            GenTile(x=t["x"], y=t["y"], tile_type=t["tile_type"], is_bridge=t.get("is_bridge", False))
            for t in tiles
        ]
        new_list, w, h = append_strip(realm["width"], realm["height"], existing, axis)
        old_set = {(t.x, t.y) for t in existing}
        added = [t for t in new_list if (t.x, t.y) not in old_set]
        self.db.insert_tiles(
            realm_id,
            [{"x": t.x, "y": t.y, "tile_type": t.tile_type, "is_bridge": False} for t in added],
        )
        self.db.update_realm(realm_id, width=w, height=h)
        return "Разведчики открыли новые земли."

    def apply_absence(self, realm_id: int) -> None:
        return RealmTickRunner(self).apply_absence(realm_id)

    def world_id_for_realm(self, realm_id: int) -> int:
        return RealmLifecycleService(self).world_id_for_realm(realm_id)

    def _world_id_for_realm(self, realm_id: int) -> int:
        return self.world_id_for_realm(realm_id)

    # ---------- daily tick ----------
    def world_tick_incomplete(self, world_id: int | None = None) -> bool:
        """Есть долины, у которых экономика ещё не догнала часы континента."""
        world = self.db.get_world(world_id) if world_id else self.db.get_or_create_world()
        if not world:
            return False
        tick = int(world.get("tick_index") or 0)
        if tick <= 0:
            return False
        for realm in self.db.list_realms_by_chain(int(world["id"])):
            # NULL - легаси "в синхроне"; отставание только при явном маркере.
            if realm.get("last_economy_tick") is None:
                continue
            if int(realm["last_economy_tick"]) < tick:
                return True
        return False

    def _require_action_window(self, realm_id: int) -> None:
        """Игровые мутации только в play при полностью догнанном тике."""
        wid = self._world_id_for_realm(realm_id)
        world = self.db.get_world(wid) or {}
        ActionWindow.require(
            tick_phase=world.get("tick_phase"),
            incomplete=self.world_tick_incomplete(wid),
        )

    def _require_continent_caught_up(self, realm_id: int) -> None:
        """Мутации запрещены, пока тик континента не завершён (фаза play)."""
        self._require_action_window(int(realm_id))

    def _require_cross_valley_caught_up(
        self, realm_a: int, realm_b: int
    ) -> None:
        # Одна долина тоже ждёт play: иначе first-click гонка внутри долины.
        self._require_action_window(int(realm_a))
        if int(realm_a) != int(realm_b):
            self._require_action_window(int(realm_b))


    def run_world_tick(
        self,
        world_id: int | None = None,
        tick_slot: int | None = None,
    ) -> dict:
        return WorldTickOrchestrator(self).run_world_tick(world_id, tick_slot)


    def run_realm_tick(
        self,
        realm_id: int,
        tick_slot: int | None = None,
        *,
        advance_clock: bool = True,
    ) -> dict:
        return RealmTickRunner(self).run_realm_tick(
            realm_id, tick_slot=tick_slot, advance_clock=advance_clock
        )

    def _prepare_tick_minor(
        self,
        realm_id: int,
        *,
        consume_pending: bool = True,
    ) -> str | None:
        return RealmTickRunner(self)._prepare_tick_minor(
            realm_id, consume_pending=consume_pending
        )

    def _active_catastrophe_refs(self, realm: dict) -> tuple[ActiveCatastropheRef, ...]:
        """Читает активные catastrophe-строки. Вызывать вне тел write-транзакций."""
        refs: list[ActiveCatastropheRef] = []
        for ev in self.db.get_active_events(int(realm["id"]), kind="catastrophe"):
            key = ev.get("event_key")
            if not key:
                continue
            resolves = ev.get("resolves_tick")
            refs.append(
                ActiveCatastropheRef(
                    key=str(key),
                    resolves_tick=None if resolves is None else int(resolves),
                )
            )
        return tuple(refs)

    def _active_tile_entity_refs(self, realm: dict) -> tuple[ActiveTileEntityRef, ...]:
        """Активные tile_entities долины (presence = status active)."""
        return tuple(
            active_tile_entity_ref(row)
            for row in self.db.list_active_tile_entities(int(realm["id"]))
        )

    def _resolve_tile_entities(
        self, realm_id: int, tick_index: int
    ) -> tuple[list[str], tuple[ActiveTileEntityRef, ...]]:
        return RealmTickRunner(self)._resolve_tile_entities(realm_id, tick_index)

    def realm_modifiers(
        self,
        realm: dict | None,
        *,
        catastrophes: Sequence[ActiveCatastropheRef] | None = None,
        tile_entities: Sequence[ActiveTileEntityRef] | None = None,
    ) -> ModifierSet:
        """Минор + катастрофы + tile_entities. Snapshot - до write-tx, collect чистый."""
        if not realm:
            return collect_active_modifiers(RealmModifierCtx())
        if catastrophes is None:
            catastrophes = self._active_catastrophe_refs(realm)
        if tile_entities is None:
            tile_entities = self._active_tile_entity_refs(realm)
        return collect_active_modifiers(
            RealmModifierCtx(
                active_minor_key=realm.get("active_minor_key"),
                active_catastrophes=catastrophes,
                active_tile_entities=tile_entities,
                tick_index=int(realm.get("tick_index") or 0),
            )
        )

    def _realm_farm_mult(self, realm: dict) -> float:
        return self.realm_modifiers(realm).farm_mult()

    def _active_cattle_plague(self, realm_id: int) -> dict | None:
        for ev in self.db.get_active_events(realm_id, kind="catastrophe"):
            if ev.get("event_key") == "cattle_plague":
                return ev
        return None

    def _resolve_active_minor_events(self, realm_id: int) -> None:
        return RealmTickRunner(self)._resolve_active_minor_events(realm_id)

    def _apply_instant_minor(self, realm_id: int, key: str) -> None:
        return RealmTickRunner(self)._apply_instant_minor(realm_id, key)

    def _feud_lines(self, realm_id: int) -> list[str]:
        return RealmTickRunner(self)._feud_lines(realm_id)

    def _sunday_extra(self, realm_id: int) -> str:
        return RealmTickRunner(self)._sunday_extra(realm_id)


    def _rumor_snapshots(
        self,
        realm_id: int,
        *,
        realm_title: str | None = None,
    ) -> list[FiefRumorSnapshot]:
        return RumorService(self)._rumor_snapshots(
            realm_id, realm_title=realm_title
        )

    def _foreign_rumor_snapshots(self, realm_id: int) -> list[FiefRumorSnapshot]:
        return RumorService(self)._foreign_rumor_snapshots(realm_id)

    def _roll_rumor_line_for_realm(self, realm_id: int) -> str | None:
        return RumorService(self)._roll_rumor_line_for_realm(realm_id)

    def _upcoming_event_hints(self, realm_id: int) -> list[UpcomingEventHint]:
        return RumorService(self)._upcoming_event_hints(realm_id)

    def _same_play_opened_mark(self, left: Any, right: Any) -> bool:
        return RumorService(self)._same_play_opened_mark(left, right)

    def plan_world_rumor_queues(self, world_id: int) -> None:
        return RumorService(self).plan_world_rumor_queues(world_id)

    def ensure_rumor_queues_planned(self, world_id: int) -> None:
        return RumorService(self).ensure_rumor_queues_planned(world_id)

    def maybe_due_rumors(
        self, world_id: int, local_now: datetime
    ) -> list[dict[str, Any]]:
        return RumorService(self).maybe_due_rumors(world_id, local_now)

    def acknowledge_rumor_posted(
        self,
        realm_id: int,
        due_iso: str,
        text: str | None,
    ) -> None:
        return RumorService(self).acknowledge_rumor_posted(
            realm_id, due_iso, text
        )

    def rumors_text(self, realm_id: int) -> str:
        return RumorService(self).rumors_text(realm_id)


    def help_text(self) -> str:
        from app.domain.guide import short_help

        return short_help()

    def guide_text(self) -> str:
        from app.domain.guide import game_guide

        return game_guide()
