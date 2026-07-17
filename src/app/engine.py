"""Игровой движок: операции над долиной через БД + доменную логику."""
from __future__ import annotations

import logging
import random
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import balance as B
from app.config import TICK_HOUR, TICK_MINUTE, TIMEZONE, tick_slots
from app.database import Database
from app.domain import absence as absence_mod
from app.domain.digest import format_decree, format_digest, format_lots_count
from app.domain.economy import (
    TileView,
    adjacent_claimable,
    fief_daily_production,
    pick_max_separated_tiles,
    render_map_parts,
    too_close_to_ruins,
)
from app.domain.holdings import format_holdings
from app.domain.map_image import (
    MapImageCache,
    MapPhoto,
    build_map_caption,
    map_fingerprint,
    render_map_image,
)
from app.domain.event_apply import (
    InstantMinorCtx,
    apply_instant_minor,
    minor_fog_ignores_patrol,
    minor_trade_bonus_frac,
    minor_upgrade_cost_mult,
    minor_wedding_gift_grain,
    realm_farm_mult,
)
from app.domain.events import (
    CATASTROPHES,
    MINOR_EVENTS,
    event_digest_line,
    minor_effect,
    next_catastrophe_delay_ticks,
    pick_catastrophe,
    roll_minor_event,
)
from app.domain.ticks import tick_active
from app.balance import best_rectangle
from app.domain.map_gen import GenTile, append_strip, coord_label, generate_map
from app.domain.portals import pick_portal_insertion
from app.domain.raids import RaidActionResult, resolve_raid, standing_raid_defense
from app.domain.rumors import (
    DailyRumorBundle,
    FiefRumorSnapshot,
    UpcomingEventHint,
    format_rumors_pull,
    parse_stored_rumors,
    roll_valley_day_rumors,
)
from app.domain.resources import (
    apply_gather_to_stash,
    fief_balance_columns,
    pending_from_row,
    stash_from_row,
)
from app.domain.tick import (
    FiefTickState,
    apply_fief_tick,
    collect_pending_bags,
)
from app.domain.tick_pipeline import (
    ActionWindow,
    TickPipeline,
    normalize_tick_phase,
    TICK_PHASE_ECONOMY,
    TICK_PHASE_PLAY,
)
from app.domain.tick_schedule import (
    format_next_tick_line,
    format_tick_slots,
    next_tick_datetime,
    schedule_anchor_at,
)

logger = logging.getLogger(__name__)


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


class Engine:
    def __init__(self, db: Database):
        self.db = db
        # Снимок усадеб континента на старте тика: слухи не смешивают
        # уже обновлённые и ещё не отыгравшие долины.
        self._rumor_snapshot_cache: dict[int, list[FiefRumorSnapshot]] | None = None
        self._map_image_cache = MapImageCache()

    # ---------- realm ----------
    def create_realm(self, chat_id: int, title: str, creator_user_id: int) -> tuple[dict, str]:
        existing = self.db.get_realm_by_chat(chat_id)
        if existing:
            raise ValueError("В этом чате долина уже основана. Используйте /вч_карта")

        width, height = best_rectangle(B.MAP_MIN_TILES)
        tiles = generate_map(width, height)
        world = self.db.get_or_create_world()
        world_id = int(world["id"])
        tz = world.get("timezone") or TIMEZONE
        rng = random.Random()
        slots = tick_slots()
        existing_realms = self.db.list_realms_by_chain(world_id)

        if not existing_realms:
            delay = next_catastrophe_delay_ticks(rng)
            first_cat = pick_catastrophe(rng, None)
            local_now = datetime.now(ZoneInfo(tz))
            # Только уже прошедшие слоты; будущие слоты дня не сжигаем.
            anchor_date, anchor_slot = schedule_anchor_at(
                local_now=local_now, slots=slots
            )
            self.db.update_world(
                world_id,
                timezone=tz,
                next_catastrophe_tick=delay,
                next_catastrophe_key=first_cat,
                last_tick_local_date=anchor_date,
                last_tick_slot=anchor_slot,
            )
            world = self.db.get_world(world_id) or world
            chain_index = 0
            neighbor_note = ""
        else:
            indices = [int(r["chain_index"]) for r in existing_realms]
            anchor_idx, _side, new_index = pick_portal_insertion(indices, rng)
            chain_index = new_index
            anchor = next(
                (r for r in existing_realms if int(r["chain_index"]) == anchor_idx),
                existing_realms[0],
            )
            neighbor_note = (
                f"\nДолина на общем континенте с <b>{anchor['title']}</b> "
                f"и остальными долинами мира."
            )

        world = self.db.get_world(world_id) or world
        world_tick = int(world.get("tick_index") or 0)
        try:
            with self.db.transaction():
                if existing_realms:
                    self.db.shift_chain_indices(world_id, chain_index, delta=1)
                realm = self.db.create_realm(
                    chat_id=chat_id,
                    title=title or "Долина",
                    width=width,
                    height=height,
                    timezone=tz,
                    tick_hour=TICK_HOUR,
                    tick_minute=TICK_MINUTE,
                    feature_flags=dict(B.DEFAULT_FEATURE_FLAGS),
                    next_catastrophe_tick=world.get("next_catastrophe_tick"),
                    world_id=world_id,
                    chain_index=chain_index,
                    day_number=int(world.get("day_number") or 1),
                    tick_index=world_tick,
                    last_tick_local_date=world.get("last_tick_local_date"),
                    last_tick_slot=world.get("last_tick_slot"),
                    next_catastrophe_key=world.get("next_catastrophe_key"),
                    pending_minor_key=world.get("pending_minor_key"),
                    active_minor_key=world.get("active_minor_key"),
                )
                self.db.update_realm(int(realm["id"]), last_economy_tick=world_tick)
                self.db.insert_tiles(
                    realm["id"],
                    [
                        {
                            "x": t.x,
                            "y": t.y,
                            "tile_type": t.tile_type,
                            "is_bridge": t.is_bridge,
                        }
                        for t in tiles
                    ],
                )
        except Exception:
            if existing_realms:
                try:
                    self.db.recompact_chain_indices(world_id)
                except Exception:
                    logger.exception(
                        "recompact_chain_indices failed after portal insert error"
                    )
            raise
        realm = self.db.get_realm(realm["id"]) or realm
        msg = (
            f"🏰 Вотчина основана: <b>{realm['title']}</b>\n"
            f"Карта {width}×{height}. День континента {realm['day_number']}. "
            f"Тики каждый день в {format_tick_slots(slots)} ({tz})."
            f"{neighbor_note}\n"
            f"Напишите боту в личку или нажмите \"Моё владение\", чтобы получить усадьбу."
        )
        return realm, msg

    def begin_wipe(self, realm_id: int) -> str:
        """Старт вайпа континента (все долины мира этой долины)."""
        realm = self.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        world_id = self._world_id_for_realm(realm_id)
        code = secrets.token_hex(3).upper()
        self.db.update_world(
            world_id,
            wipe_confirm_code=code,
            wipe_confirm_until=_utcnow() + timedelta(minutes=10),
        )
        n = len(self.db.list_realms_by_chain(world_id))
        return (
            f"⚠️ Удаление <b>всего континента</b> ({n} долин), якорь id={realm_id}.\n"
            f"Чтобы подтвердить, отправьте:\n"
            f"<code>/вч_wipe {realm_id} {code} УДАЛИТЬ</code>\n"
            f"Код действует 10 минут. Отдельная долина не стирается - только весь мир."
        )

    def confirm_wipe(self, realm_id: int, code: str, confirm_word: str) -> str:
        realm = self.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        if confirm_word != "УДАЛИТЬ":
            raise ValueError("Нужно слово УДАЛИТЬ")
        world_id = self._world_id_for_realm(realm_id)
        world = self.db.get_world(world_id) or {}
        until = world.get("wipe_confirm_until")
        if not world.get("wipe_confirm_code") or not until or until < _utcnow():
            raise ValueError("Нет активного кода. Сначала /вч_wipe_start")
        if code.upper() != str(world["wipe_confirm_code"]).upper():
            raise ValueError("Неверный код")
        realms = self.db.list_realms_by_chain(world_id)
        for r in realms:
            self.db.delete_realm(int(r["id"]))
        self.db.update_world(
            world_id,
            wipe_confirm_code=None,
            wipe_confirm_until=None,
            day_number=1,
            tick_index=0,
            forced_tick_count=0,
            active_minor_key=None,
            pending_minor_key=None,
            next_catastrophe_tick=None,
            next_catastrophe_key=None,
            last_catastrophe_key=None,
            last_tick_at=None,
            last_tick_local_date=None,
            last_tick_slot=None,
        )
        return f"Континент стёрт ({len(realms)} долин). Можно снова /вотчина."

    # ---------- join / onboarding ----------
    def ensure_user(self, user) -> None:
        name = (user.full_name or user.first_name or "Путник").strip()
        self.db.upsert_user(user.id, user.username, name)
        # подтягиваем имя усадеб под username / полное имя (без дублей "Артём")
        self.db.set_fief_names_for_user(user.id, fief_name_for_user(user))

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

    def starter_tile_choices(self, realm_id: int, count: int = 3) -> list[dict]:
        """Предлагает стартовые клетки, максимально разнесённые на торе."""
        realm = self.db.get_realm(realm_id)
        width, height = int(realm["width"]), int(realm["height"])
        tiles = self.db.get_tiles(realm_id)
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
        realm = self.db.get_realm(realm_id) or {}
        world_id = realm.get("world_id")
        if world_id is None:
            return False
        owned = self.db.get_fief_by_user_world(user_id, int(world_id))
        return owned is not None and int(owned["realm_id"]) != int(realm_id)

    def join_fief(
        self,
        realm_id: int,
        user,
        tile_id: int,
    ) -> tuple[dict, str]:
        self.ensure_user(user)
        existing = self.db.get_fief_by_user(realm_id, user.id)
        if existing:
            raise ValueError("У вас уже есть усадьба в этой долине")
        realm = self.db.get_realm(realm_id)
        if not realm or realm.get("world_id") is None:
            raise ValueError("Долина не привязана к континенту")
        owned = self.db.get_fief_by_user_world(user.id, int(realm["world_id"]))
        if owned:
            raise ValueError(
                "У вас уже есть усадьба на континенте. "
                "Вторая усадьба недоступна."
            )

        tile = self.db._fetchone("SELECT * FROM map_tiles WHERE id=%s AND realm_id=%s;", (tile_id, realm_id))
        if not tile or tile["owner_fief_id"] is not None:
            raise ValueError("Клетка недоступна")
        if tile["tile_type"] in (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER, B.TILE_RUINS):
            raise ValueError("Нельзя начать здесь")

        width, height = int(realm["width"]), int(realm["height"])
        tiles = self.db.get_tiles(realm_id)
        ruins = [
            (int(t["x"]), int(t["y"]))
            for t in tiles
            if t["tile_type"] == B.TILE_RUINS
        ]
        if too_close_to_ruins(int(tile["x"]), int(tile["y"]), ruins, width, height):
            raise ValueError("Нельзя начать здесь")

        name = fief_name_for_user(user)
        fief = self.db.create_fief(
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
        self.db.update_tile(
            tile["id"],
            owner_fief_id=fief["id"],
            building=B.BLD_MANOR,
            building_level=B.STARTING_MANOR_LEVEL,
            is_core=True,
        )
        self.db.set_last_realm(user.id, realm_id)
        self.maybe_grow_map(realm_id)
        return fief, (
            f"🏡 {name} основана на {coord_label(tile['x'], tile['y'])} "
            f"({B.TILE_NAMES_RU[tile['tile_type']]}).\n"
            f"Стартовый набор: двор (главная клетка), {B.STARTING_GRAIN} зерна, "
            f"{B.STARTING_GOODS} товаров, {B.STARTING_MIGHT} силы.\n"
            f"Урожай собирается сам. Первый квест - занять соседнюю клетку "
            f"(от {B.CLAIM_COSTS[2]} товаров)."
        )

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

    def status_card(self, fief_id: int) -> str:
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
        lines = [
            f"🏡 <b>{self.fief_label(fief)}</b> · день {realm['day_number']}",
            "",
        ]
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
        if alerts:
            lines.extend(alerts)
            lines.append("")
        defense = standing_raid_defense(
            watch_defense=prod.defense,
            victim_might=int(fief.get("might") or 0),
            patrol_active=tick_active(fief.get("patrol_until_tick"), tick_index),
            fog_ignores_patrol=minor_fog_ignores_patrol(realm.get("active_minor_key")),
        )
        lines.extend(
            [
                (
                    f"⚡ Действия: {fief['actions']}/{B.ACTIONS_BANK_MAX} · "
                    f"Клетки: {len(tiles)}/{B.TILE_HARD_CAP}"
                ),
                (
                    f"🌾 {fief['grain']} · 📦 {fief['goods']} · "
                    f"⚔️ {fief['might']} · 🛡 {defense}"
                ),
                _stash_status_line(barn),
                "",
                (
                    f"+{prod.grain:.0f} зерна/день, +{prod.goods:.0f} товаров/день, "
                    f"+{prod.might:.0f} силы/день"
                ),
                f"Корм: земля {land}, дружина {militia}",
                "",
            ]
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
        lines.append(format_next_tick_line(next_at, local_now=local_now))
        if notes:
            lines.append("· " + " · ".join(notes))
        return "\n".join(lines)

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
        text = f"🗺️ {realm['title']} (день {realm['day_number']})\n<pre>{grid}</pre>"
        if footer:
            text += f"\n\n{footer}"
        return text

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
        fingerprint = map_fingerprint(
            realm_id=int(realm["id"]),
            width=int(realm["width"]),
            height=int(realm["height"]),
            tiles=views,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
        )
        caption, caption_extra = build_map_caption(
            title=str(realm["title"]),
            day_number=int(realm["day_number"]),
            footer=footer,
        )
        cached = self._map_image_cache.get(fingerprint)
        if cached is not None:
            return MapPhoto(
                png_bytes=cached.png_bytes,
                caption=caption,
                fingerprint=fingerprint,
                file_id=cached.file_id,
                caption_extra=caption_extra,
            )
        png_bytes = render_map_image(
            int(realm["width"]),
            int(realm["height"]),
            views,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
        )
        self._map_image_cache.put_png(fingerprint, png_bytes)
        return MapPhoto(
            png_bytes=png_bytes,
            caption=caption,
            fingerprint=fingerprint,
            file_id=None,
            caption_extra=caption_extra,
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
        fief = self.db.get_fief(fief_id)
        self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        tiles = self.db.fief_tiles(fief_id)
        n = len([t for t in tiles if not t.get("is_overgrown")]) + 1
        if n > B.TILE_HARD_CAP:
            raise ValueError("Достигнут предел клеток")
        realm_id = fief["realm_id"]
        target = self.db.get_tile(realm_id, x, y)
        if not target:
            raise ValueError("Клетка не существует")
        if target["owner_fief_id"] is not None and not target.get("is_overgrown"):
            raise ValueError("Клетка занята")
        realm = self.db.get_realm(realm_id)
        views = {(t.x, t.y): t for t in self.tile_views(realm_id)}
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
        if target.get("is_overgrown"):
            prev = target.get("owner_fief_id")
            cost = B.claim_cost(n, is_wilds=False)
            if fief["goods"] < cost:
                raise ValueError(f"Нужно {cost} товаров")
            with self.db.transaction():
                self._spend_action(fief)
                if not self.db.debit_fief_resources(fief_id, goods=cost):
                    raise ValueError(f"Нужно {cost} товаров")
                if prev and prev != fief_id:
                    comp = absence_mod.compensation_for_claim(cost)
                    prev_f = self.db.get_fief(prev)
                    if prev_f:
                        self.db.update_fief(prev, goods=prev_f["goods"] + comp)
                self.db.update_tile(
                    target["id"],
                    owner_fief_id=fief_id,
                    is_overgrown=False,
                    is_core=(n <= 2),
                    building=None,
                    building_level=0,
                    damaged=False,
                )
                if n == 2:
                    for t in self.db.fief_tiles(fief_id):
                        self.db.update_tile(t["id"], is_core=True)
                self.maybe_grow_map(realm_id)
            self._onboard_claim(fief_id)
            return f"Занята заросшая клетка {coord_label(x, y)} (−{cost} товаров)."

        cost = B.claim_cost(n, is_wilds=is_wilds)
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров (у вас {fief['goods']})")

        new_type = target["tile_type"]
        ruins_loot = 0
        if is_wilds:
            new_type = random.choice(B.WILDS_CLEAR_TO)
        if new_type == B.TILE_RUINS and not target.get("ruins_looted"):
            ruins_loot = random.randint(B.RUINS_LOOT_MIN, B.RUINS_LOOT_MAX)

        with self.db.transaction():
            self._spend_action(fief)
            if not self.db.debit_fief_resources(fief_id, goods=cost):
                raise ValueError(f"Нужно {cost} товаров (у вас {fief['goods']})")

            if ruins_loot:
                fief = self.db.get_fief(fief_id)
                cap = B.stash_cap(self.barn_level(fief_id))
                add = min(ruins_loot, max(0, cap - fief["goods"]))
                self.db.update_fief(fief_id, goods=fief["goods"] + add)

            self.db.update_tile(
                target["id"],
                owner_fief_id=fief_id,
                tile_type=new_type,
                is_core=(n <= 2),
                ruins_looted=True if new_type == B.TILE_RUINS or target.get("ruins_looted") else target.get("ruins_looted"),
                is_overgrown=False,
            )
            if n == 2:
                for t in self.db.fief_tiles(fief_id):
                    self.db.update_tile(t["id"], is_core=True)

            self.maybe_grow_map(realm_id)
        self._onboard_claim(fief_id)
        extra = f" Находка в руинах: +{ruins_loot} товаров." if ruins_loot else ""
        if is_wilds:
            extra = f" Глушь расчищена → {B.TILE_NAMES_RU[new_type]}." + extra
        return f"Клетка {coord_label(x, y)} присоединена (−{cost} товаров).{extra}"

    def build_or_upgrade(self, fief_id: int, x: int, y: int, building: str) -> str:
        if building not in B.BUILDING_COSTS:
            raise ValueError("Неизвестное здание")
        fief = self.db.get_fief(fief_id)
        self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        tile = self.db.get_tile(fief["realm_id"], x, y)
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
            with self.db.transaction():
                self._spend_action(fief)
                if not self.db.debit_fief_resources(fief_id, goods=cost):
                    raise ValueError(f"Ремонт: нужно {cost} товаров")
                self.db.update_tile(tile["id"], damaged=False)
            self._onboard_build(fief_id)
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
        realm = self.db.get_realm(fief["realm_id"])
        cost = B.scaled_building_cost(
            cost, minor_upgrade_cost_mult(realm.get("active_minor_key"))
        )
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров")
        with self.db.transaction():
            self._spend_action(fief)
            if not self.db.debit_fief_resources(fief_id, goods=cost):
                raise ValueError(f"Нужно {cost} товаров")
            self.db.update_tile(
                tile["id"],
                building=building,
                building_level=target_level,
                damaged=False,
            )
        self._onboard_build(fief_id)
        return f"{B.BUILDING_NAMES_RU[building]} {target_level} на {coord_label(x, y)} (−{cost} товаров)."

    def demolish_building(self, fief_id: int, x: int, y: int) -> str:
        """Снос здания на клетке: 1 действие, возврат доли вложенных товаров."""
        fief = self.db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        tile = self.db.get_tile(fief["realm_id"], x, y)
        if not tile or tile["owner_fief_id"] != fief_id:
            raise ValueError("Это не ваша клетка")
        if tile.get("is_overgrown"):
            raise ValueError("Клетка заросла")
        building = tile.get("building")
        level = int(tile.get("building_level") or 0)
        if not building or level <= 0:
            raise ValueError("На клетке нет здания")
        if building == B.BLD_MANOR or tile.get("is_core"):
            raise ValueError("Главную клетку с двором снести нельзя")
        if building not in B.BUILDING_COSTS:
            raise ValueError("Это здание нельзя снести")
        refund = B.demolish_refund_goods(building, level)
        with self.db.transaction():
            self._spend_action(fief)
            fief = self.db.get_fief(fief_id)
            self.db.update_fief(fief_id, goods=int(fief["goods"]) + refund)
            self.db.update_tile(
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
        if resource not in (B.RES_GRAIN, B.RES_GOODS, B.RES_MIGHT):
            raise ValueError("Можно собрать зерно, товары или силу")
        fief = self.db.get_fief(fief_id)
        if not fief:
            raise ValueError("Усадьба не найдена")
        if fief.get("frozen"):
            raise ValueError("Усадьба заморожена")
        amount = B.gather_amount(resource)
        self.collect_for_fief(fief_id, include_might=(resource != B.RES_MIGHT))
        fief = self.db.get_fief(fief_id)
        with self.db.transaction():
            self._spend_action(fief)
            fief = self.db.get_fief(fief_id)
            barn = self.barn_level(fief_id)
            cap = B.stash_cap(barn)
            stash, gained = apply_gather_to_stash(
                stash_from_row(fief), resource, amount, cap=cap
            )
            self.db.update_fief(fief_id, **{resource: stash[resource]})
            if resource == B.RES_MIGHT:
                return f"Сбор: +{gained} силы (−1 действие)."
            if resource == B.RES_GRAIN:
                suffix = "" if gained == amount else " (склад почти полон)"
                return f"Сбор: +{gained} зерна (−1 действие).{suffix}"
            suffix = "" if gained == amount else " (склад почти полон)"
            return f"Сбор: +{gained} товаров (−1 действие).{suffix}"

    def _onboard_claim(self, fief_id: int) -> None:
        fief = self.db.get_fief(fief_id)
        patch = try_complete_onboard_claim(fief)
        if patch:
            self.db.update_fief(fief_id, **patch)

    def _onboard_build(self, fief_id: int) -> None:
        fief = self.db.get_fief(fief_id)
        patch = try_complete_onboard_build(fief)
        if patch:
            self.db.update_fief(fief_id, **patch)

    def patrol(self, fief_id: int) -> str:
        fief = self.db.get_fief(fief_id)
        cost = int(B.PATROL_COST_MIGHT)
        if cost > 0 and fief["might"] < cost:
            raise ValueError(f"Нужно {cost} силы")
        with self.db.transaction():
            self._spend_action(fief)
            realm = self.db.get_realm(fief["realm_id"]) or {}
            tick_index = int(realm.get("tick_index") or 0)
            if cost > 0:
                if not self.db.debit_fief_resources(fief_id, might=cost):
                    raise ValueError(f"Нужно {cost} силы")
            self.db.update_fief(
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

    def contribute_catastrophe_might(
        self, event_id: int, user_id: int, amount: int = 5
    ) -> int:
        """Вклад силы в активную катастрофу. Возвращает сумму в котле."""
        amt = int(amount)
        if amt <= 0:
            raise ValueError("Недостаточно силы")
        ev = self.db.get_event(event_id)
        if not ev or ev.get("status") != "active":
            raise ValueError("Событие уже завершено")
        fief = self.db.get_fief_by_user(ev["realm_id"], user_id)
        if not fief:
            raise ValueError("Сначала получите усадьбу в личке")
        self._require_action_window(int(fief["realm_id"]))
        with self.db.transaction():
            ev = self.db.get_event(event_id)
            if not ev or ev.get("status") != "active":
                raise ValueError("Событие уже завершено")
            self._require_action_window(int(fief["realm_id"]))
            if not self.db.debit_fief_resources(int(fief["id"]), might=amt):
                raise ValueError("Недостаточно силы")
            self.db.bump_event_action(event_id, int(fief["id"]), "might", amt)
            total = sum(
                int(a.get("amount") or 0) for a in self.db.event_actions(event_id)
            )
        return total

    def list_raid_target_fiefs(self, attacker_fief_id: int) -> list[dict]:
        """Цели на всём континенте (без своей user_id)."""
        atk = self.db.get_fief(attacker_fief_id)
        if not atk:
            return []
        atk_uid = int(atk["user_id"])
        atk_realm = int(atk["realm_id"])
        realm_ids = {atk_realm}
        for nb in self.db.list_adjacent_realms(atk_realm):
            realm_ids.add(int(nb["id"]))
        out: list[dict] = []
        for rid in sorted(realm_ids):
            for f in self.db.list_fiefs(rid):
                if f.get("frozen"):
                    continue
                if int(f["id"]) == int(attacker_fief_id):
                    continue
                if int(f["user_id"]) == atk_uid:
                    continue
                item = dict(f)
                item["via_portal"] = int(f["realm_id"]) != atk_realm
                out.append(item)
        return out

    def raid(self, attacker_id: int, victim_id: int, might: int) -> RaidActionResult:
        if might < B.RAID_MIN_MIGHT:
            raise ValueError(f"Минимум {B.RAID_MIN_MIGHT} силы")
        atk = self.require_active_fief(attacker_id)
        vic = self.db.get_fief(victim_id)
        if not atk or not vic:
            raise ValueError("Цель не найдена")
        same_realm = int(atk["realm_id"]) == int(vic["realm_id"])
        if not self.db.realms_are_adjacent(
            int(atk["realm_id"]), int(vic["realm_id"])
        ):
            raise ValueError("Цель не найдена")
        self._require_cross_valley_caught_up(
            int(atk["realm_id"]), int(vic["realm_id"])
        )
        if atk["id"] == vic["id"]:
            raise ValueError("Нельзя грабить себя")
        if int(atk["user_id"]) == int(vic["user_id"]):
            raise ValueError("Нельзя грабить свою усадьбу")
        if atk["hungry"]:
            raise ValueError("Голодные мужики не воюют")
        if atk["might"] < might:
            raise ValueError("Недостаточно силы")
        realm = self.db.get_realm(atk["realm_id"]) or {}
        vic_realm = self.db.get_realm(vic["realm_id"]) or realm
        tick_index = int(realm.get("tick_index") or 0)
        cross_valley = not same_realm
        if tick_active(atk.get("shield_until_tick"), tick_index):
            raise ValueError("Пока действует щит, набеги недоступны")
        if tick_active(vic.get("shield_until_tick"), tick_index):
            raise ValueError("У жертвы щит после набега")
        last_pair = self.db.last_raid_attacker_victim(attacker_id, victim_id)
        last_reverse = self.db.last_raid_attacker_victim(victim_id, attacker_id)
        for raid_tick in (last_pair, last_reverse):
            if raid_tick is None:
                continue
            if int(raid_tick) + B.RAID_SAME_VICTIM_TICKS > tick_index:
                raise ValueError("Кулдаун на эту пару усадеб")

        self.collect_for_fief(attacker_id)
        self.collect_for_fief(victim_id, include_might=False)
        atk = self.db.get_fief(attacker_id)
        vic = self.db.get_fief(victim_id)

        fog = minor_fog_ignores_patrol(realm.get("active_minor_key")) or (
            minor_fog_ignores_patrol(vic_realm.get("active_minor_key"))
        )
        watch_def = self.fief_prod(vic).defense
        patrol = tick_active(vic.get("patrol_until_tick"), tick_index)
        intercept = False
        interceptor = None
        # Пока континент догоняет тик - чужие долины не тратят силу на перехват.
        incomplete_world = self.world_tick_incomplete(
            self._world_id_for_realm(int(vic["realm_id"]))
        )
        if vic.get("pact_id"):
            for m in self.db.pact_members(vic["pact_id"]):
                if m["id"] == vic["id"]:
                    continue
                if not m.get("cover_allies"):
                    continue
                if incomplete_world and int(m["realm_id"]) != int(vic["realm_id"]):
                    continue
                if m["might"] >= B.INTERCEPT_MIGHT:
                    intercept = True
                    interceptor = m
                    break

        atk_label = self.fief_label(atk)
        vic_label = self.fief_label(vic)
        result = resolve_raid(
            attacker_name=atk_label,
            victim_name=vic_label,
            attack_might=might,
            watch_defense=watch_def,
            patrol_active=patrol,
            intercept=intercept,
            victim_grain=vic["grain"],
            victim_goods=vic["goods"],
            barn_level=self.barn_level(victim_id),
            victim_daily_grain=self.fief_prod(vic).grain,
            victim_daily_goods=self.fief_prod(vic).goods,
            fog_ignores_patrol=fog,
            victim_might=int(vic.get("might") or 0),
        )

        atk_line = result.public_line
        vic_line = result.public_line
        if cross_valley:
            atk_valley = realm.get("title") or "Долина"
            vic_valley = vic_realm.get("title") or "Долина"
            atk_line = f"В \"{vic_valley}\": {result.public_line}"
            vic_line = f"Из \"{atk_valley}\": {result.public_line}"

        with self.db.transaction():
            self._require_cross_valley_caught_up(
                int(atk["realm_id"]), int(vic["realm_id"])
            )
            self._spend_action(atk)
            self.db.update_fief(
                attacker_id,
                last_raid_at=_utcnow(),
                last_raid_tick=tick_index,
            )
            # CAS-промах перехватчика: пересчёт без него (дозор/стража), набег не рвём.
            if interceptor:
                if not self.db.debit_fief_resources(
                    int(interceptor["id"]), might=int(B.INTERCEPT_MIGHT)
                ):
                    interceptor = None
                    result = resolve_raid(
                        attacker_name=atk_label,
                        victim_name=vic_label,
                        attack_might=might,
                        watch_defense=watch_def,
                        patrol_active=patrol,
                        intercept=False,
                        victim_grain=vic["grain"],
                        victim_goods=vic["goods"],
                        barn_level=self.barn_level(victim_id),
                        victim_daily_grain=self.fief_prod(vic).grain,
                        victim_daily_goods=self.fief_prod(vic).goods,
                        fog_ignores_patrol=fog,
                        victim_might=int(vic.get("might") or 0),
                    )
                    atk_line = result.public_line
                    vic_line = result.public_line
                    if cross_valley:
                        atk_valley = realm.get("title") or "Долина"
                        vic_valley = vic_realm.get("title") or "Долина"
                        atk_line = f"В \"{vic_valley}\": {result.public_line}"
                        vic_line = f"Из \"{atk_valley}\": {result.public_line}"
            if result.might_lost > 0:
                if not self.db.debit_fief_resources(
                    attacker_id, might=int(result.might_lost)
                ):
                    raise ValueError("Недостаточно силы")

            if result.success:
                vic = self.db.get_fief(victim_id)
                atk = self.db.get_fief(attacker_id)
                self.db.update_fief(
                    victim_id,
                    grain=vic["grain"] - result.grain_stolen,
                    goods=vic["goods"] - result.goods_stolen,
                    shield_until=None,
                    shield_until_tick=tick_index + B.RAID_VICTIM_SHIELD_TICKS,
                )
                barn = self.barn_level(attacker_id)
                cap = B.stash_cap(barn)
                g_add = min(result.grain_stolen, max(0, cap - atk["grain"]))
                d_add = min(result.goods_stolen, max(0, cap - atk["goods"]))
                self.db.update_fief(attacker_id, grain=atk["grain"] + g_add, goods=atk["goods"] + d_add)

            self.db.log_raid(
                realm_id=int(atk["realm_id"]),
                victim_realm_id=int(vic["realm_id"]),
                attacker_fief_id=attacker_id,
                victim_fief_id=victim_id,
                success=result.success,
                might_spent=might,
                grain_stolen=result.grain_stolen,
                goods_stolen=result.goods_stolen,
                public_line=atk_line,
                tick_index=tick_index,
            )
            digest_by_realm: dict[int, str] = {
                int(atk["realm_id"]): atk_line,
                int(vic["realm_id"]): vic_line,
            }
            for rid, line in digest_by_realm.items():
                r = self.db.get_realm(rid) or {}
                lines = list(r.get("pending_raid_lines") or [])
                lines.append(line)
                self.db.update_realm(rid, pending_raid_lines=lines)

            vic_final = self.db.get_fief(victim_id) or vic
            atk_final = self.db.get_fief(attacker_id) or atk
        return RaidActionResult(
            public_line=atk_line,
            success=result.success,
            victim_fief_id=victim_id,
            victim_user_id=int(vic_final["user_id"]),
            victim_name=self.fief_label(vic_final),
            attacker_name=self.fief_label(atk_final),
            grain_stolen=result.grain_stolen,
            goods_stolen=result.goods_stolen,
            intercept_applied=result.intercept_applied,
            interceptor_fief_id=int(interceptor["id"]) if interceptor else None,
            interceptor_user_id=int(interceptor["user_id"]) if interceptor else None,
            attacker_realm_id=int(atk_final["realm_id"]),
            victim_realm_id=int(vic_final["realm_id"]),
            via_portal=cross_valley,
            attacker_public_line=atk_line,
            victim_public_line=vic_line,
        )

    # ---------- trade ----------
    def post_trade(
        self,
        fief_id: int,
        give_res: str,
        give_amt: int,
        want_res: str,
        want_amt: int,
        target_fief_id: int | None = None,
    ) -> str:
        if give_res not in B.TRADEABLE or want_res not in B.TRADEABLE:
            raise ValueError("Можно менять только зерно и товары")
        if give_res == want_res:
            raise ValueError("Разные ресурсы")
        if give_amt <= 0 or want_amt <= 0:
            raise ValueError("Количество должно быть > 0")
        fief = self.db.get_fief(fief_id)
        self._require_action_window(int(fief["realm_id"]))
        if target_fief_id is not None:
            target = self.db.get_fief(target_fief_id)
            if not target:
                raise ValueError("Усадьба не найдена")
            self._require_cross_valley_caught_up(
                int(fief["realm_id"]), int(target["realm_id"])
            )
        self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        have = fief["grain"] if give_res == B.RES_GRAIN else fief["goods"]
        if have < give_amt:
            raise ValueError("Недостаточно ресурса для предложения")
        with self.db.transaction():
            fief = self.db.get_fief(fief_id)
            if target_fief_id is not None:
                target = self.db.get_fief(target_fief_id)
                if not target:
                    raise ValueError("Усадьба не найдена")
                self._require_cross_valley_caught_up(
                    int(fief["realm_id"]), int(target["realm_id"])
                )
            have = fief["grain"] if give_res == B.RES_GRAIN else fief["goods"]
            if have < give_amt:
                raise ValueError("Недостаточно ресурса для предложения")
            # Ресурс остаётся на усадьбе (доступен набегам) до момента сделки.
            realm = self.db.get_realm(fief["realm_id"]) or {}
            tick_index = int(realm.get("tick_index") or 0)
            offer = self.db.create_trade(
                realm_id=fief["realm_id"],
                offerer_fief_id=fief_id,
                target_fief_id=target_fief_id,
                give_res=give_res,
                give_amt=give_amt,
                want_res=want_res,
                want_amt=want_amt,
                expires_tick=tick_index + B.TRADE_EXPIRE_TICKS,
            )
        return f"Лот #{offer['id']}: отдаю {give_amt} {B.RES_NAMES_RU[give_res]} за {want_amt} {B.RES_NAMES_RU[want_res]}."

    def accept_trade(self, fief_id: int, trade_id: int) -> str:
        trade = self.db.get_trade(trade_id)
        if not trade:
            raise ValueError("Лот недоступен")
        if trade["status"] != "open":
            return "Лот уже закрыт или недоступен."
        if trade["offerer_fief_id"] == fief_id:
            raise ValueError("Свой лот")
        if trade.get("target_fief_id") and trade["target_fief_id"] != fief_id:
            raise ValueError("Лот адресован другому")
        buyer = self.db.get_fief(fief_id)
        if not self.db.realms_are_adjacent(
            int(buyer["realm_id"]), int(trade["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._require_cross_valley_caught_up(
            int(buyer["realm_id"]), int(trade["realm_id"])
        )
        self.collect_for_fief(fief_id)
        buyer = self.db.get_fief(fief_id)
        want = trade["want_res"]
        want_amt = trade["want_amt"]
        have = buyer["grain"] if want == B.RES_GRAIN else buyer["goods"]
        if have < want_amt:
            raise ValueError("Недостаточно ресурса для оплаты")

        realm = self.db.get_realm(buyer["realm_id"])
        tick_index = int(realm.get("tick_index") or 0)
        bonus = minor_trade_bonus_frac(realm.get("active_minor_key"))
        wedding_gift = minor_wedding_gift_grain(realm.get("active_minor_key"))

        with self.db.transaction():
            live = self.db.get_trade(trade_id)
            if not live or live["status"] != "open":
                return "Лот уже закрыт или недоступен."
            buyer = self.db.get_fief(fief_id)
            self._require_cross_valley_caught_up(
                int(buyer["realm_id"]), int(live["realm_id"])
            )
            expires_tick = live.get("expires_tick")
            if expires_tick is None or int(expires_tick) <= tick_index:
                self._refund_trade(live)
                expired = True
            else:
                expired = False
                claimed = self.db.claim_open_trade(trade_id)
                if not claimed:
                    return "Лот уже закрыт или недоступен."
                trade = claimed

                give_amt = int(trade["give_amt"])
                give_res = trade["give_res"]
                seller = self.db.get_fief(trade["offerer_fief_id"])
                if not seller:
                    raise ValueError("Усадьба продавца не найдена")
                seller_have = (
                    seller["grain"] if give_res == B.RES_GRAIN else seller["goods"]
                )
                if seller_have < give_amt:
                    raise ValueError("У продавца недостаточно ресурса для сделки")

                buyer = self.db.get_fief(fief_id)
                have = buyer["grain"] if want == B.RES_GRAIN else buyer["goods"]
                if have < want_amt:
                    raise ValueError("Недостаточно ресурса для оплаты")

                if give_res == B.RES_GRAIN:
                    self.db.update_fief(
                        int(seller["id"]), grain=int(seller["grain"]) - give_amt
                    )
                else:
                    self.db.update_fief(
                        int(seller["id"]), goods=int(seller["goods"]) - give_amt
                    )

                if want == B.RES_GRAIN:
                    self.db.update_fief(fief_id, grain=buyer["grain"] - want_amt)
                else:
                    self.db.update_fief(fief_id, goods=buyer["goods"] - want_amt)

                pay_bonus = int(want_amt * bonus)
                recv_bonus = int(give_amt * bonus)

                seller = self.db.get_fief(trade["offerer_fief_id"])
                if want == B.RES_GRAIN:
                    self.db.update_fief(
                        seller["id"],
                        grain=seller["grain"] + want_amt + pay_bonus,
                    )
                else:
                    self.db.update_fief(
                        seller["id"],
                        goods=seller["goods"] + want_amt + pay_bonus,
                    )

                buyer = self.db.get_fief(fief_id)
                cap = B.stash_cap(self.barn_level(fief_id))
                if give_res == B.RES_GRAIN:
                    add = min(give_amt + recv_bonus, max(0, cap - buyer["grain"]))
                    self.db.update_fief(fief_id, grain=buyer["grain"] + add)
                else:
                    add = min(give_amt + recv_bonus, max(0, cap - buyer["goods"]))
                    self.db.update_fief(fief_id, goods=buyer["goods"] + add)

                if wedding_gift:
                    for fid in (fief_id, trade["offerer_fief_id"]):
                        f = self.db.get_fief(fid)
                        self.db.update_fief(fid, grain=f["grain"] + wedding_gift)
        if expired:
            raise ValueError("Лот истёк")
        return f"Сделка #{trade_id} закрыта."

    def cancel_trade(self, fief_id: int, trade_id: int) -> str:
        """Снять свой лот. Ресурс не возвращаем - при выставлении его не снимали."""
        trade = self.db.get_trade(trade_id)
        if not trade or trade["offerer_fief_id"] != fief_id or trade["status"] != "open":
            raise ValueError("Нельзя отменить")
        with self.db.transaction():
            claimed = self.db.claim_cancel_open_trade(int(trade_id))
            if not claimed:
                raise ValueError("Лот уже закрыт или недоступен")
        return f"Лот #{trade_id} снят с рынка."

    def send_resources(
        self,
        from_fief_id: int,
        to_fief_id: int,
        res: str,
        amt: int,
    ) -> str:
        """Односторонняя передача зерна/товаров (на доверии, без эскроу)."""
        if res not in B.TRADEABLE:
            raise ValueError("Можно передать только зерно или товары")
        if amt <= 0:
            raise ValueError("Количество должно быть > 0")
        if from_fief_id == to_fief_id:
            raise ValueError("Нельзя передать себе")

        sender = self.require_active_fief(from_fief_id)
        receiver = self.db.get_fief(to_fief_id)
        if not sender or not receiver:
            raise ValueError("Усадьба не найдена")
        if not self.db.realms_are_adjacent(
            int(sender["realm_id"]), int(receiver["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._require_cross_valley_caught_up(
            int(sender["realm_id"]), int(receiver["realm_id"])
        )
        if int(sender["user_id"]) == int(receiver["user_id"]):
            raise ValueError("Нельзя передать своей другой усадьбе")
        if sender.get("frozen") or receiver.get("frozen"):
            raise ValueError("Усадьба недоступна")

        self.collect_for_fief(from_fief_id)
        self.collect_for_fief(to_fief_id)

        with self.db.transaction():
            sender = self.db.get_fief(from_fief_id)
            receiver = self.db.get_fief(to_fief_id)
            if not sender or not receiver:
                raise ValueError("Усадьба не найдена")
            self._require_cross_valley_caught_up(
                int(sender["realm_id"]), int(receiver["realm_id"])
            )
            have = sender["grain"] if res == B.RES_GRAIN else sender["goods"]
            if have < amt:
                raise ValueError("Недостаточно ресурса")

            cap = B.stash_cap(self.barn_level(to_fief_id))
            held = receiver["grain"] if res == B.RES_GRAIN else receiver["goods"]
            free = max(0, cap - held)
            if free < amt:
                raise ValueError(
                    f"У получателя не хватает места на складе "
                    f"(свободно {free}, нужно {amt})"
                )

            if res == B.RES_GRAIN:
                self.db.update_fief(from_fief_id, grain=sender["grain"] - amt)
                self.db.update_fief(to_fief_id, grain=receiver["grain"] + amt)
            else:
                self.db.update_fief(from_fief_id, goods=sender["goods"] - amt)
                self.db.update_fief(to_fief_id, goods=receiver["goods"] + amt)

        res_name = B.RES_NAMES_RU[res]
        return (
            f"Передано {amt} {res_name} усадьбе {self.fief_label(receiver)}."
        )

    def _refund_trade(self, trade: dict) -> None:
        """Снять истёкший лот. Ресурс не трогаем: он не уходит в эскроу при выставлении."""
        with self.db.transaction():
            self.db.claim_cancel_open_trade(int(trade["id"]))

    def market_text(self, realm_id: int, fief_id: int | None = None) -> str:
        offers = self.db.list_open_trades(realm_id, fief_id)
        if not offers:
            return "🛒 Рынок пуст."
        lines = ["🛒 Рынок:"]
        for o in offers:
            seller = self.db.get_fief(int(o["offerer_fief_id"]))
            seller_label = self.fief_label(seller) if seller else "Усадьба"
            tgt = ", только вам" if o.get("target_fief_id") else ""
            lines.append(
                f"#{o['id']} ({seller_label}): отдаёт {o['give_amt']} "
                f"{B.RES_NAMES_RU[o['give_res']]} за {o['want_amt']} "
                f"{B.RES_NAMES_RU[o['want_res']]}{tgt}"
            )
        return "\n".join(lines)

    # ---------- pacts ----------
    def create_pact(self, fief_id: int, name: str) -> str:
        fief = self.db.get_fief(fief_id)
        if fief.get("pact_id"):
            raise ValueError("Вы уже в пакте")
        self._require_action_window(int(fief["realm_id"]))
        name = name.strip()[:40]
        if not name:
            raise ValueError("Нужно имя")
        pact = self.db.create_pact(fief["realm_id"], name, fief_id)
        return f"Пакт \"{pact['name']}\" создан. Приглашайте союзников."

    def invite_to_pact(self, founder_fief_id: int, target_fief_id: int) -> dict:
        """Создаёт открытое приглашение. Не меняет pact_id цели."""
        founder = self.db.get_fief(founder_fief_id)
        target = self.db.get_fief(target_fief_id)
        if not founder or not target:
            raise ValueError("Усадьба не найдена")
        if not founder.get("pact_id"):
            raise ValueError("Сначала создайте пакт")
        pact = self.db.get_pact(founder["pact_id"])
        if not pact or pact["founder_fief_id"] != founder_fief_id:
            raise ValueError("Приглашает только основатель")
        members = self.db.pact_members(pact["id"])
        if len(members) >= B.PACT_SIZE_MAX:
            raise ValueError("Пакт полон")
        if target.get("pact_id"):
            raise ValueError("Цель уже в пакте")
        if not self.db.realms_are_adjacent(
            int(founder["realm_id"]), int(target["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._require_cross_valley_caught_up(
            int(founder["realm_id"]), int(target["realm_id"])
        )
        if founder_fief_id == target_fief_id:
            raise ValueError("Нельзя пригласить себя")
        if self.db.get_open_pact_invite(pact["id"], target_fief_id):
            raise ValueError("Приглашение уже отправлено")
        realm = self.db.get_realm(founder["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        with self.db.transaction():
            founder = self.db.get_fief(founder_fief_id)
            target = self.db.get_fief(target_fief_id)
            if not founder or not target:
                raise ValueError("Усадьба не найдена")
            self._require_cross_valley_caught_up(
                int(founder["realm_id"]), int(target["realm_id"])
            )
            invite = self.db.create_pact_invite(
                realm_id=founder["realm_id"],
                pact_id=pact["id"],
                inviter_fief_id=founder_fief_id,
                target_fief_id=target_fief_id,
                expires_tick=tick_index + B.PACT_INVITE_EXPIRE_TICKS,
            )
        return invite

    def accept_pact_invite(self, target_fief_id: int, invite_id: int) -> str:
        invite = self.db.get_pact_invite(invite_id)
        if not invite or invite["status"] != "open":
            raise ValueError("Приглашение недоступно")
        realm = self.db.get_realm(invite["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        expires_tick = invite.get("expires_tick")
        if expires_tick is None or int(expires_tick) <= tick_index:
            self.db.update_pact_invite(invite_id, status="expired")
            raise ValueError("Приглашение истекло")
        if int(invite["target_fief_id"]) != int(target_fief_id):
            raise ValueError("Это приглашение не вам")
        target = self.db.get_fief(target_fief_id)
        if not target:
            raise ValueError("Усадьба не найдена")
        if target.get("pact_id"):
            raise ValueError("Вы уже в пакте")
        pact = self.db.get_pact(invite["pact_id"])
        if not pact:
            raise ValueError("Пакт распущен")
        if not self.db.realms_are_adjacent(
            int(target["realm_id"]), int(pact["realm_id"])
        ):
            raise ValueError("Другой континент")
        self._require_cross_valley_caught_up(
            int(target["realm_id"]), int(pact["realm_id"])
        )
        members = self.db.pact_members(pact["id"])
        if len(members) >= B.PACT_SIZE_MAX:
            raise ValueError("Пакт полон")
        with self.db.transaction():
            target = self.db.get_fief(target_fief_id)
            pact = self.db.get_pact(invite["pact_id"])
            if not target:
                raise ValueError("Усадьба не найдена")
            if not pact:
                raise ValueError("Пакт распущен")
            self._require_cross_valley_caught_up(
                int(target["realm_id"]), int(pact["realm_id"])
            )
            claimed = self.db.claim_open_pact_invite(invite_id, "accepted")
            if not claimed:
                raise ValueError("Приглашение недоступно")
            members = self.db.pact_members(pact["id"])
            if len(members) >= B.PACT_SIZE_MAX:
                raise ValueError("Пакт полон")
            target = self.db.get_fief(target_fief_id)
            if target.get("pact_id"):
                raise ValueError("Вы уже в пакте")
            self.db.update_fief(target_fief_id, pact_id=pact["id"], cover_allies=False)
        return f"Вы в пакте \"{pact['name']}\"."

    def decline_pact_invite(self, actor_fief_id: int, invite_id: int) -> str:
        invite = self.db.get_pact_invite(invite_id)
        if not invite or invite["status"] != "open":
            raise ValueError("Приглашение недоступно")
        actor = self.db.get_fief(actor_fief_id)
        if not actor:
            raise ValueError("Усадьба не найдена")
        is_target = int(invite["target_fief_id"]) == int(actor_fief_id)
        is_inviter = int(invite["inviter_fief_id"]) == int(actor_fief_id)
        if not is_target and not is_inviter:
            raise ValueError("Нельзя отклонить чужое приглашение")
        status = "cancelled" if is_inviter and not is_target else "declined"
        claimed = self.db.claim_open_pact_invite(invite_id, status)
        if not claimed:
            raise ValueError("Приглашение недоступно")
        return "Приглашение отклонено." if status == "declined" else "Приглашение отменено."

    def leave_pact(self, fief_id: int) -> str:
        fief = self.db.get_fief(fief_id)
        if not fief.get("pact_id"):
            raise ValueError("Вы не в пакте")
        self._require_action_window(int(fief["realm_id"]))
        with self.db.transaction():
            fief = self.db.get_fief(fief_id)
            if not fief.get("pact_id"):
                raise ValueError("Вы не в пакте")
            self._require_action_window(int(fief["realm_id"]))
            pact_id = fief["pact_id"]
            pact = self.db.get_pact(pact_id)
            remaining = [
                m
                for m in self.db.pact_members(pact_id)
                if int(m["id"]) != int(fief_id)
            ]
            if len(remaining) < B.PACT_SIZE_MIN:
                self.db.update_fief(fief_id, pact_id=None)
                self.db.dissolve_pact(pact_id)
                return "Вы вышли. Пакт распущен (меньше 2 участников)."
            self.db.update_fief(fief_id, pact_id=None)
            if pact and pact["founder_fief_id"] == fief_id and remaining:
                self.db._update(
                    "pacts", pact_id, {"founder_fief_id": remaining[0]["id"]}
                )
            return "Вы вышли из пакта."

    def set_cover(self, fief_id: int, enabled: bool) -> str:
        self.db.update_fief(fief_id, cover_allies=enabled)
        return "Прикрытие союзников: " + ("вкл" if enabled else "выкл")

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
        realm = self.db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        for fief in self.db.list_fiefs(realm_id):
            last_active_tick = fief.get("last_active_tick")
            ticks = (
                tick_index - int(last_active_tick)
                if last_active_tick is not None
                else B.OVERGROWN_TICKS
            )
            tier = absence_mod.inactivity_tier(ticks)
            if tier != "overgrown":
                continue
            tiles = self.db.fief_tiles(fief["id"])
            cores = [t for t in tiles if t.get("is_core")]
            if len(cores) < 1:
                # пометить первую как core
                if tiles:
                    self.db.update_tile(tiles[0]["id"], is_core=True)
                    cores = [tiles[0]]
            core_ids = {t["id"] for t in cores[:2]}
            for t in tiles:
                if t["id"] not in core_ids and not t.get("is_overgrown"):
                    self.db.update_tile(t["id"], is_overgrown=True)

    def _world_id_for_realm(self, realm_id: int) -> int:
        realm = self.db.get_realm(realm_id) or {}
        wid = realm.get("world_id")
        if wid is not None:
            return int(wid)
        return int(self.db.get_or_create_world()["id"])

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

    def _enter_tick_economy(
        self, world_id: int, world: dict | None = None
    ) -> None:
        if (
            world is not None
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY
        ):
            return
        self.db.update_world(int(world_id), **TickPipeline.economy_fields())
        if world is not None:
            world["tick_phase"] = TICK_PHASE_ECONOMY

    def _enter_tick_play(
        self,
        world_id: int,
        world: dict | None = None,
        **extra: Any,
    ) -> None:
        if (
            world is not None
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_PLAY
            and not extra
        ):
            return
        fields = {**TickPipeline.play_fields(), **extra}
        self.db.update_world(int(world_id), **fields)
        if world is not None:
            world.update(fields)

    def run_world_tick(
        self,
        world_id: int | None = None,
        tick_slot: int | None = None,
    ) -> dict:
        """Один тик континента: общие часы/события, локальные сводки и слухи.

        Часы двигаются один раз; экономика каждой долины идемпотентна по
        last_economy_tick. При обрыве следующий вызов догоняет отстающие долины
        без повторного сдвига tick_index и календарного дня.
        """
        world = self.db.get_world(world_id) if world_id else self.db.get_or_create_world()
        if not world:
            raise ValueError("Континент не найден")
        wid = int(world["id"])
        realms = self.db.list_realms_by_chain(wid)
        if not realms:
            # Пустой континент не двигает часы; play чтобы не зависнуть в economy.
            self._enter_tick_play(wid, world)
            return {"world_id": wid, "realms": [], "digest": None, "chat_id": None}

        current = int(world.get("tick_index") or 0)
        # Легаси/новая колонка: NULL значит "уже на текущих часах", не "отстаёт".
        for r in realms:
            if r.get("last_economy_tick") is None:
                self.db.update_realm(int(r["id"]), last_economy_tick=current)
                r["last_economy_tick"] = current

        economies_done = all(
            int(r.get("last_economy_tick") or -1) >= current for r in realms
        )
        # Crash после fan-out, до enter_play: закрыть окно без нового тика.
        if (
            current > 0
            and economies_done
            and normalize_tick_phase(world.get("tick_phase")) == TICK_PHASE_ECONOMY
        ):
            play_fields: dict[str, Any] = {}
            if world.get("pending_minor_key") is None:
                play_fields["pending_minor_key"] = (
                    roll_minor_event(random.Random()) or ""
                )
            self._enter_tick_play(wid, world, **play_fields)
            self.db.sync_realms_clock_from_world(wid)
            return {
                "world_id": wid,
                "realms": [],
                "digest": None,
                "chat_id": None,
                "resumed": True,
                "incomplete": False,
            }

        resuming = any(
            int(r.get("last_economy_tick") or -1) < current for r in realms
        ) and current > 0

        if resuming:
            new_tick = current
            self._enter_tick_economy(wid, world)
        else:
            new_tick = current + 1
            pending_raw = world.get("pending_minor_key")
            if pending_raw is None:
                minor_key = roll_minor_event(random.Random())
            else:
                minor_key = pending_raw or None

            tz = ZoneInfo(world.get("timezone") or TIMEZONE)
            local_now = datetime.now(tz)
            local_date = local_now.date()
            day = int(world.get("day_number") or 1)
            world_fields: dict[str, Any] = {
                "tick_index": new_tick,
                "day_number": day,
                "last_tick_at": _utcnow(),
                "active_minor_key": minor_key,
                "active_minor_until": None,
                "pending_minor_key": None,
                **TickPipeline.economy_fields(),
            }
            # Плановые слоты двигает только scheduler (когда передан tick_slot).
            # Админский тик без слота: tick_index двигаем, календарный день и слоты - нет.
            if tick_slot is not None:
                slots = tick_slots()
                tick_slot = max(0, min(int(tick_slot), max(0, len(slots) - 1)))
                prev_local = _as_date(world.get("last_tick_local_date"))
                # Календарный день: +1 только когда курсор last_tick_local_date
                # переходит на новую локальную дату (не на каждый слот и не при NULL).
                if prev_local is not None and local_date > prev_local:
                    world_fields["day_number"] = day + 1
                world_fields["last_tick_local_date"] = local_date
                world_fields["last_tick_slot"] = tick_slot
            # Часы мира + зеркала долин - один COMMIT.
            # Иначе crash между update_world и sync оставляет economy на stale realm clock.
            with self.db.transaction():
                self.db.update_world(wid, **world_fields)
                self.db.sync_realms_clock_from_world(wid)
            world.update(world_fields)

        realm_results = []
        # Единый снимок на этот вызов тика (при догоне после сбоя - best-effort
        # по текущей БД, без отдельного персиста start-of-tick).
        self._rumor_snapshot_cache = {
            int(r["id"]): self._rumor_snapshots(int(r["id"]))
            for r in self.db.list_realms_by_chain(wid)
        }
        try:
            for realm in self.db.list_realms_by_chain(wid):
                rid = int(realm["id"])
                if int(realm.get("last_economy_tick") or -1) >= new_tick:
                    realm_results.append(
                        {
                            "realm_id": rid,
                            "skipped": True,
                            "already_ticked": True,
                            "digest": None,
                            "chat_id": realm.get("chat_id"),
                        }
                    )
                    continue
                try:
                    with self.db.transaction():
                        result = self.run_realm_tick(
                            rid,
                            tick_slot=tick_slot,
                            advance_clock=False,
                        )
                        self.db.update_realm(rid, last_economy_tick=new_tick)
                    realm_results.append(result)
                except Exception:
                    logger.exception("realm tick failed world=%s realm=%s", wid, rid)
                    realm_results.append(
                        {
                            "realm_id": rid,
                            "skipped": True,
                            "error": True,
                            "digest": None,
                            "chat_id": realm.get("chat_id"),
                        }
                    )
        finally:
            self._rumor_snapshot_cache = None

        caught_up = all(
            int(r.get("last_economy_tick") or -1) >= new_tick
            for r in self.db.list_realms_by_chain(wid)
        )
        if caught_up:
            world = self.db.get_world(wid) or world
            play_fields: dict[str, Any] = {}
            if world.get("pending_minor_key") is None:
                play_fields["pending_minor_key"] = (
                    roll_minor_event(random.Random()) or ""
                )
            self._enter_tick_play(wid, world, **play_fields)
            self.db.sync_realms_clock_from_world(wid)

        posted = [x for x in realm_results if not x.get("skipped")]
        head = posted[0] if posted else (realm_results[0] if realm_results else {})
        return {
            "world_id": wid,
            "realms": realm_results,
            "digest": head.get("digest"),
            "chat_id": head.get("chat_id"),
            "resumed": resuming,
            "incomplete": not caught_up,
        }

    def run_realm_tick(
        self,
        realm_id: int,
        tick_slot: int | None = None,
        *,
        advance_clock: bool = True,
    ) -> dict:
        """Тик одной долины. При advance_clock=False часы уже выставлены миром."""
        if advance_clock:
            # Одиночный вызов (админ/тесты) гоняет весь континент.
            world_id = self._world_id_for_realm(realm_id)
            world_result = self.run_world_tick(world_id, tick_slot=tick_slot)
            for item in world_result.get("realms") or []:
                if int(item.get("realm_id") or 0) == int(realm_id):
                    return item
            if world_result.get("realms"):
                return world_result["realms"][0]
            return world_result

        realm = self.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        tick_index = int(realm.get("tick_index") or 0)
        day = int(realm.get("day_number") or 1)
        self.apply_absence(realm_id)
        for t in self.db.list_expired_open_trades(realm_id, tick_index):
            self._refund_trade(t)

        event_line = self._prepare_tick_minor(realm_id, consume_pending=False)
        realm = self.db.get_realm(realm_id) or realm
        base_farm_mult = self._realm_farm_mult(realm)

        outcomes = []
        for fief in self.db.list_fiefs(realm_id):
            if fief.get("frozen"):
                continue
            tiles = [
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
            if not tick_active(fief.get("patrol_until_tick"), tick_index):
                if fief.get("patrol_until_tick") is not None or fief.get("patrol_until"):
                    self.db.update_fief(
                        fief["id"],
                        patrol_until=None,
                        patrol_until_tick=None,
                    )
            if not tick_active(fief.get("shield_until_tick"), tick_index):
                if fief.get("shield_until_tick") is not None or fief.get("shield_until"):
                    self.db.update_fief(
                        fief["id"],
                        shield_until=None,
                        shield_until_tick=None,
                    )

            # Неактивная долина владельца: без урожая и без +действия.
            if not self.fief_is_active_play(fief):
                continue

            farm_mult = base_farm_mult

            state = FiefTickState.from_fief_row(
                fief,
                tiles,
                self.barn_level(fief["id"]),
                farm_mult=farm_mult,
            )
            out = apply_fief_tick(state)
            self.db.update_fief(
                fief["id"],
                **out.balance_columns(),
                actions=out.actions,
                hungry=out.hungry,
            )
            outcomes.append((fief, out))

        feud_lines = self._feud_lines(realm_id)
        raid_lines = list(realm.get("pending_raid_lines") or [])
        self.db.update_realm(realm_id, pending_raid_lines=[])

        offers = self.db.list_open_trades(realm_id)
        market_line = None
        if offers:
            best = max(offers, key=lambda o: o["give_amt"] + o["want_amt"])
            market_line = (
                f"{format_lots_count(len(offers))}. Лучший: отдаёт "
                f"{best['give_amt']} {B.RES_NAMES_RU[best['give_res']]} за "
                f"{best['want_amt']} {B.RES_NAMES_RU[best['want_res']]}"
            )

        tz = ZoneInfo(realm.get("timezone") or TIMEZONE)
        local_now = datetime.now(tz)
        local_date = local_now.date()

        sunday_extra = None
        if local_date.weekday() == 6:
            sunday_extra = self._sunday_extra(realm_id)

        grow_msg = self.maybe_grow_map(realm_id)
        realm = self.db.get_realm(realm_id) or realm
        rumor_bundle = self._roll_day_rumors(realm_id)
        digest = format_digest(
            realm_title=realm["title"],
            day=day,
            night_lines=raid_lines,
            event_line=event_line,
            market_line=market_line,
            feud_lines=feud_lines,
            sunday_extra=sunday_extra,
            rumor_lines=rumor_bundle.local,
            foreign_rumor_lines=rumor_bundle.foreign,
        )
        if grow_msg:
            digest += f"\n📜 {grow_msg}"

        self.db.update_realm(
            realm_id,
            last_digest_text=digest,
            last_rumor_lines=rumor_bundle.as_storage(),
        )

        return {
            "realm_id": int(realm_id),
            "digest": digest,
            "chat_id": realm["chat_id"],
            "outcomes": outcomes,
        }

    def _prepare_tick_minor(
        self,
        realm_id: int,
        *,
        consume_pending: bool = True,
    ) -> str | None:
        """Берёт заранее свёрстанный минор (для слухов) или роллит заново.

        consume_pending=False: часы/ключ уже выставлены континентом - только эффекты.
        """
        realm = self.db.get_realm(realm_id)
        if not realm:
            return None

        tick_index = int(realm.get("tick_index") or 0)
        self._resolve_active_minor_events(realm_id)
        if consume_pending:
            pending_raw = realm.get("pending_minor_key")
            if pending_raw is None:
                minor_key = roll_minor_event(random.Random())
            else:
                minor_key = pending_raw or None
                self.db.update_realm(realm_id, pending_minor_key=None)
        else:
            minor_key = realm.get("active_minor_key") or None
        if not minor_key:
            if consume_pending:
                self.db.update_realm(
                    realm_id, active_minor_key=None, active_minor_until=None
                )
            return None
        if minor_key not in MINOR_EVENTS:
            if consume_pending:
                self.db.update_realm(
                    realm_id, active_minor_key=None, active_minor_until=None
                )
            return None

        meta = MINOR_EVENTS[minor_key]
        narrative = meta["canned_narrative"]
        duration_t = int(minor_effect(minor_key).get("duration_ticks") or 1)
        resolves_tick = tick_index + duration_t
        if consume_pending:
            self.db.update_realm(
                realm_id,
                active_minor_key=minor_key,
                active_minor_until=None,
            )
        event_line = event_digest_line(meta)
        self._apply_instant_minor(realm_id, minor_key)
        # Засуха остаётся active до следующего тика (farm_mult), без личного выкупа.
        status = "active" if minor_key == "drought" else "resolved"
        self.db.create_event(
            realm_id=realm_id,
            kind="minor",
            event_key=minor_key,
            payload={},
            narrative=narrative,
            status=status,
            resolves_tick=resolves_tick,
        )
        return event_line

    def _realm_farm_mult(self, realm: dict) -> float:
        cat_keys = [
            str(ev["event_key"])
            for ev in self.db.get_active_events(int(realm["id"]), kind="catastrophe")
            if ev.get("event_key")
        ]
        return realm_farm_mult(
            active_minor_key=realm.get("active_minor_key"),
            active_catastrophe_keys=cat_keys,
        )

    def _active_cattle_plague(self, realm_id: int) -> dict | None:
        for ev in self.db.get_active_events(realm_id, kind="catastrophe"):
            if ev.get("event_key") == "cattle_plague":
                return ev
        return None

    def _resolve_active_minor_events(self, realm_id: int) -> None:
        for ev in self.db.get_active_events(realm_id, kind="minor"):
            self.db.update_event(ev["id"], status="resolved")

    def _apply_instant_minor(self, realm_id: int, key: str) -> None:
        apply_instant_minor(
            key,
            InstantMinorCtx(
                fiefs=list(self.db.list_fiefs(realm_id)),
                barn_level=self.barn_level,
                fief_tiles=self.db.fief_tiles,
                update_fief=self.db.update_fief,
                update_tile=self.db.update_tile,
                rng=random,
            ),
        )

    def _feud_lines(self, realm_id: int) -> list[str]:
        realm = self.db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        since_tick = max(0, tick_index - B.FEUD_WINDOW_TICKS)
        raids = self.db.raids_since_tick(realm_id, since_tick)
        counts: dict[tuple[int, int], int] = {}
        for r in raids:
            key = (r["attacker_fief_id"], r["victim_fief_id"])
            counts[key] = counts.get(key, 0) + 1
        lines = []
        for (a, v), c in counts.items():
            if c >= B.FEUD_RAIDS_IN_WINDOW:
                af = self.db.get_fief(a)
                vf = self.db.get_fief(v)
                if af and vf:
                    lines.append(f"{self.fief_label(af)} против {self.fief_label(vf)}")
        return lines

    def _sunday_extra(self, realm_id: int) -> str:
        fiefs = self.db.list_fiefs(realm_id)
        if not fiefs:
            return ""
        by_tiles = sorted(
            fiefs,
            key=lambda f: len(self.db.fief_tiles(f["id"])),
            reverse=True,
        )
        top = by_tiles[0]
        return f"Титулы: больше всех земель - {self.fief_label(top)}."

    def _rumor_snapshots(
        self,
        realm_id: int,
        *,
        realm_title: str | None = None,
    ) -> list[FiefRumorSnapshot]:
        realm = self.db.get_realm(realm_id) or {}
        tick_index = int(realm.get("tick_index") or 0)
        title = (
            str(realm_title)
            if realm_title is not None
            else str(realm.get("title") or "")
        )
        out: list[FiefRumorSnapshot] = []
        for fief in self.db.list_fiefs(realm_id):
            if fief.get("frozen"):
                continue
            buildings = tuple(
                (str(t["building"]), int(t["building_level"]))
                for t in self.db.fief_tiles(fief["id"])
                if t.get("building")
                and int(t.get("building_level") or 0) > 0
                and not t.get("is_overgrown")
            )
            out.append(
                FiefRumorSnapshot(
                    fief_id=int(fief["id"]),
                    name=self.fief_label(fief),
                    grain=int(fief["grain"]),
                    goods=int(fief["goods"]),
                    might=int(fief["might"]),
                    buildings=buildings,
                    patrol_active=tick_active(fief.get("patrol_until_tick"), tick_index),
                    realm_title=title,
                )
            )
        return out

    def _foreign_rumor_snapshots(self, realm_id: int) -> list[FiefRumorSnapshot]:
        """Усадьбы других долин того же континента (для чужих сплетен)."""
        cache = self._rumor_snapshot_cache
        if cache is not None:
            out: list[FiefRumorSnapshot] = []
            for rid, snaps in cache.items():
                if int(rid) == int(realm_id):
                    continue
                out.extend(snaps)
            return out
        out = []
        for nb in self.db.list_adjacent_realms(realm_id):
            title = str(nb.get("title") or "долина")
            out.extend(
                self._rumor_snapshots(int(nb["id"]), realm_title=title)
            )
        return out

    def _roll_day_rumors(self, realm_id: int) -> DailyRumorBundle:
        # Местные - живой снимок после экономики долины; чужие - из кэша
        # старта тика, чтобы не смешивать уже отыгравшие и ещё нет.
        return roll_valley_day_rumors(
            self._rumor_snapshots(realm_id),
            self._foreign_rumor_snapshots(realm_id),
            random.Random(),
            event_hints=self._upcoming_event_hints(realm_id),
        )

    def _upcoming_event_hints(self, realm_id: int) -> list[UpcomingEventHint]:
        realm = self.db.get_realm(realm_id) or {}
        hints: list[UpcomingEventHint] = []
        pending = realm.get("pending_minor_key")
        if pending:
            hints.append(UpcomingEventHint(kind="minor", key=str(pending)))
        next_tick = realm.get("next_catastrophe_tick")
        next_key = realm.get("next_catastrophe_key")
        tick_index = int(realm.get("tick_index") or 0)
        if (
            next_tick is not None
            and next_key
            and int(next_tick) - tick_index <= B.RUMOR_CATASTROPHE_WARN_TICKS
            and int(next_tick) > tick_index
        ):
            hints.append(UpcomingEventHint(kind="catastrophe", key=str(next_key)))
        return hints

    def rumors_text(self, realm_id: int) -> str:
        realm = self.db.get_realm(realm_id)
        if not realm:
            return format_rumors_pull([])
        bundle = parse_stored_rumors(realm.get("last_rumor_lines"))
        return format_rumors_pull(bundle.local, foreign_lines=bundle.foreign)

    def help_text(self) -> str:
        from app.domain.guide import short_help

        return short_help()

    def guide_text(self) -> str:
        from app.domain.guide import game_guide

        return game_guide()
