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
from app.domain.events import (
    CATASTROPHES,
    MINOR_EVENTS,
    catastrophe_effect,
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
from app.domain.raids import RaidActionResult, resolve_raid
from app.domain.rumors import (
    DailyRumorBundle,
    FiefRumorSnapshot,
    UpcomingEventHint,
    format_rumors_pull,
    parse_stored_rumors,
    roll_valley_day_rumors,
)
from app.domain.tick import FiefTickState, apply_fief_tick, collect_pending
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
            # Только уже прошедшие слоты; 13:00/19:00 впереди не сжигаем.
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
        """У игрока уже есть усадьба (на континенте допускается только одна)."""
        for f in self.db.list_fiefs_by_user(user_id):
            if int(f["realm_id"]) != int(realm_id):
                return True
        return False

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
        owned = self.db.list_fiefs_by_user(user.id)
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

        realm = self.db.get_realm(realm_id)
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
        g, d, m, pg, pd, pm, notes = collect_pending(
            fief["grain"],
            fief["goods"],
            fief["might"],
            fief["pending_grain"],
            fief["pending_goods"],
            fief["pending_might"],
            barn,
            include_might=include_might,
        )
        self.db.update_fief(
            fief_id,
            grain=g,
            goods=d,
            might=m,
            pending_grain=pg,
            pending_goods=pd,
            pending_might=pm,
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
        lines.extend(
            [
                (
                    f"⚡ Действия: {fief['actions']}/{B.ACTIONS_BANK_MAX} · "
                    f"Клетки: {len(tiles)}/{B.TILE_HARD_CAP}"
                ),
                f"🌾 {fief['grain']} · 📦 {fief['goods']} · ⚔️ {fief['might']}",
                _stash_status_line(barn),
                "",
                (
                    f"В день: +{prod.grain:.0f} зерна, +{prod.goods:.0f} товаров, "
                    f"+{prod.might:.0f} силы"
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
        vote_line = self.force_tick_status_line(int(fief["realm_id"]))
        if vote_line:
            lines.append(vote_line)
        if notes:
            lines.append("· " + " · ".join(notes))
        return "\n".join(lines)

    def map_text(self, realm_id: int, highlight_fief_id: int | None = None) -> str:
        realm = self.db.get_realm(realm_id)
        views = self.tile_views(realm_id)
        fiefs = {f["id"]: f for f in self.db.list_fiefs(realm_id)}
        legend = {}
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

    def _spend_action(self, fief: dict) -> None:
        if fief["actions"] < 1:
            raise ValueError("Нет действий на сегодня (макс. запас 3)")
        if fief["frozen"]:
            raise ValueError("Усадьба заморожена")
        if not self.fief_is_active_play(fief):
            raise ValueError(
                "Сначала выберите эту долину активной "
                "(меню усадьбы / список долин в /start)"
            )
        realm = self.db.get_realm(fief["realm_id"]) or {}
        self.db.update_fief(
            fief["id"],
            actions=fief["actions"] - 1,
            last_active_at=_utcnow(),
            last_active_tick=int(realm.get("tick_index") or 0),
        )

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
                self.db.update_fief(fief_id, goods=fief["goods"] - cost)
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
            fief = self.db.get_fief(fief_id)
            self.db.update_fief(fief_id, goods=fief["goods"] - cost)

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
            self._spend_action(fief)
            fief = self.db.get_fief(fief_id)
            self.db.update_fief(fief_id, goods=fief["goods"] - cost)
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
        if realm.get("active_minor_key") == "good_stone":
            cost = int(cost * 0.75)
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров")
        self._spend_action(fief)
        fief = self.db.get_fief(fief_id)
        self.db.update_fief(fief_id, goods=fief["goods"] - cost)
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
        self._spend_action(fief)
        fief = self.db.get_fief(fief_id)
        if resource == B.RES_MIGHT:
            self.db.update_fief(fief_id, might=int(fief["might"]) + amount)
            return f"Сбор: +{amount} силы (−1 действие)."
        barn = self.barn_level(fief_id)
        cap = B.stash_cap(barn)
        if resource == B.RES_GRAIN:
            room = max(0, cap - int(fief["grain"]))
            gained = min(amount, room)
            self.db.update_fief(fief_id, grain=int(fief["grain"]) + gained)
            suffix = "" if gained == amount else " (склад почти полон)"
            return f"Сбор: +{gained} зерна (−1 действие).{suffix}"
        room = max(0, cap - int(fief["goods"]))
        gained = min(amount, room)
        self.db.update_fief(fief_id, goods=int(fief["goods"]) + gained)
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
        self._spend_action(fief)
        fief = self.db.get_fief(fief_id)
        realm = self.db.get_realm(fief["realm_id"]) or {}
        tick_index = int(realm.get("tick_index") or 0)
        new_might = fief["might"] - cost if cost > 0 else fief["might"]
        self.db.update_fief(
            fief_id,
            might=new_might,
            patrol_until=None,
            patrol_until_tick=tick_index + B.PATROL_TICKS,
        )
        if cost > 0:
            return (
                f"Дозор выставлен на {B.PATROL_TICKS} тик(а) "
                f"(−{cost} силы)."
            )
        return f"Дозор выставлен на {B.PATROL_TICKS} тик(а)."

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
        last_raid_tick = atk.get("last_raid_tick")
        if (
            last_raid_tick is not None
            and int(last_raid_tick) + B.RAID_ATTACKER_COOLDOWN_TICKS > tick_index
        ):
            raise ValueError("Ещё рано для нового набега")
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

        fog = (
            realm.get("active_minor_key") == "fog"
            or vic_realm.get("active_minor_key") == "fog"
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
            atk = self.db.get_fief(attacker_id)
            self.db.update_fief(
                attacker_id,
                might=atk["might"] - result.might_lost,
                last_raid_at=_utcnow(),
                last_raid_tick=tick_index,
            )
            if interceptor:
                self.db.update_fief(interceptor["id"], might=interceptor["might"] - B.INTERCEPT_MIGHT)

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
        bonus = 0.05 if realm.get("active_minor_key") == "fair" else 0.0
        wedding = realm.get("active_minor_key") == "wedding"

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

                if wedding:
                    gift = int(minor_effect("wedding").get("trade_gift_grain") or 5)
                    for fid in (fief_id, trade["offerer_fief_id"]):
                        f = self.db.get_fief(fid)
                        self.db.update_fief(fid, grain=f["grain"] + gift)
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
        with self.db.transaction():
            fief = self.db.get_fief(fief_id)
            if not fief.get("pact_id"):
                raise ValueError("Вы не в пакте")
            pact_id = fief["pact_id"]
            pact = self.db.get_pact(pact_id)
            remaining = [
                m
                for m in self.db.pact_members(pact_id)
                if int(m["id"]) != int(fief_id)
            ]
            if len(remaining) < B.PACT_SIZE_MIN:
                leaver_realm = int(fief["realm_id"])
                if any(int(m["realm_id"]) != leaver_realm for m in remaining):
                    self._require_continent_caught_up(leaver_realm)
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

    # ---------- force tick vote (continent) ----------
    def force_tick_eligible_fiefs(self, realm_id: int) -> list[dict]:
        """Совместимость: усадьбы долины. Для прогресса используйте world-пул."""
        return [f for f in self.db.list_fiefs(realm_id) if not f.get("frozen")]

    def _world_id_for_realm(self, realm_id: int) -> int:
        realm = self.db.get_realm(realm_id) or {}
        wid = realm.get("world_id")
        if wid is not None:
            return int(wid)
        return int(self.db.get_or_create_world()["id"])

    def force_tick_eligible_fiefs_world(self, world_id: int) -> list[dict]:
        out: list[dict] = []
        for realm in self.db.list_realms_by_chain(world_id):
            out.extend(
                f for f in self.db.list_fiefs(int(realm["id"])) if not f.get("frozen")
            )
        return out

    def force_tick_progress(self, realm_id: int) -> dict[str, Any]:
        world_id = self._world_id_for_realm(realm_id)
        eligible = self.force_tick_eligible_fiefs_world(world_id)
        eligible_ids = {int(f["id"]) for f in eligible}
        n = len(eligible)
        vote_ids = {
            int(v["fief_id"])
            for v in self.db.list_world_force_tick_votes(world_id)
            if int(v["fief_id"]) in eligible_ids
        }
        needed = B.force_tick_votes_needed(n)
        return {
            "eligible": n,
            "votes": len(vote_ids),
            "needed": needed,
            "available": n >= B.FORCE_TICK_MIN_PLAYERS,
            "vote_fief_ids": vote_ids,
            "world_id": world_id,
        }

    def force_tick_status_line(self, realm_id: int) -> str | None:
        progress = self.force_tick_progress(realm_id)
        if not progress["available"]:
            return None
        return f"Голоса: {progress['votes']}/{progress['needed']}"

    def _forced_tick_mandate_open(self, world_id: int) -> bool:
        """Есть ли ещё кворум голосов за досрочный тик на континенте."""
        eligible = self.force_tick_eligible_fiefs_world(world_id)
        n = len(eligible)
        if n < B.FORCE_TICK_MIN_PLAYERS:
            return False
        eligible_ids = {int(f["id"]) for f in eligible}
        votes = sum(
            1
            for v in self.db.list_world_force_tick_votes(world_id)
            if int(v["fief_id"]) in eligible_ids
        )
        return votes >= B.force_tick_votes_needed(n)

    def cast_force_tick_vote(self, fief_id: int) -> dict[str, Any]:
        """Голос за досрочный тик континента. При пороге - run_world_tick(forced).

        Голоса не сбрасываем здесь: сброс только после успешного сдвига часов
        в run_world_tick(forced=True), иначе падение до тика теряет мандат.
        """
        fief = self.require_active_fief(fief_id)
        if fief.get("frozen"):
            raise ValueError("Усадьба заморожена")
        realm_id = int(fief["realm_id"])
        world_id = self._world_id_for_realm(realm_id)

        with self.db.transaction():
            added = self.db.add_force_tick_vote(realm_id, fief_id)
            progress = self.force_tick_progress(realm_id)
            if not progress["available"]:
                return {
                    "status": "too_few",
                    "progress": progress,
                    "fief": fief,
                }
            if progress["votes"] < progress["needed"]:
                return {
                    "status": "voted" if added else "already",
                    "progress": progress,
                    "fief": fief,
                }

        tick_result = self.run_world_tick(world_id, forced=True)
        # Incomplete/resume или нет мандата: дня не форсировали - не врём "forced".
        # Голоса остаются до реального сдвига часов после догона.
        if tick_result.get("forced_skipped") or tick_result.get("resumed"):
            return {
                "status": "voted" if added else "already",
                "progress": self.force_tick_progress(realm_id),
                "fief": fief,
            }
        return {
            "status": "forced",
            "progress": self.force_tick_progress(realm_id),
            "fief": fief,
            "tick": tick_result,
        }

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

    def _require_continent_caught_up(self, realm_id: int) -> None:
        """Междолинные мутации запрещены, пока часть долин не догнала тик."""
        if self.world_tick_incomplete(self._world_id_for_realm(realm_id)):
            raise ValueError(
                "Континент ещё догоняет тик. "
                "Междолинные действия временно недоступны."
            )

    def _require_cross_valley_caught_up(
        self, realm_a: int, realm_b: int
    ) -> None:
        if int(realm_a) == int(realm_b):
            return
        self._require_continent_caught_up(int(realm_a))

    def run_world_tick(
        self,
        world_id: int | None = None,
        tick_slot: int | None = None,
        *,
        forced: bool = False,
    ) -> dict:
        """Один тик континента: общие часы/события, локальные сводки и слухи.

        Часы двигаются один раз; экономика каждой долины идемпотентна по
        last_economy_tick. При обрыве следующий вызов догоняет отстающие долины
        без повторного сдвига дня.
        """
        world = self.db.get_world(world_id) if world_id else self.db.get_or_create_world()
        if not world:
            raise ValueError("Континент не найден")
        wid = int(world["id"])
        realms = self.db.list_realms_by_chain(wid)
        if not realms:
            return {"world_id": wid, "realms": [], "digest": None, "chat_id": None}

        current = int(world.get("tick_index") or 0)
        # Легаси/новая колонка: NULL значит "уже на текущих часах", не "отстаёт".
        for r in realms:
            if r.get("last_economy_tick") is None:
                self.db.update_realm(int(r["id"]), last_economy_tick=current)
                r["last_economy_tick"] = current

        resuming = any(
            int(r.get("last_economy_tick") or -1) < current for r in realms
        ) and current > 0

        if resuming:
            new_tick = current
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
            day = int(world.get("day_number") or 1) + 1
            world_fields: dict[str, Any] = {
                "tick_index": new_tick,
                "day_number": day,
                "last_tick_at": _utcnow(),
                "active_minor_key": minor_key,
                "active_minor_until": None,
                "pending_minor_key": None,
            }
            # Плановые слоты двигает только scheduler (tick_slot без forced).
            # Досрочный/ручной тик часы мира двигает, расписание 13:00/19:00 - нет.
            if tick_slot is not None and not forced:
                slots = tick_slots()
                tick_slot = max(0, min(int(tick_slot), max(0, len(slots) - 1)))
                world_fields["last_tick_local_date"] = local_date
                world_fields["last_tick_slot"] = tick_slot
            if forced:
                world_fields["forced_tick_count"] = (
                    int(world.get("forced_tick_count") or 0) + 1
                )
            # Часы мира + зеркала долин + сброс голосов - один COMMIT.
            # Иначе crash между update_world и sync оставляет economy на stale realm clock.
            # Голоса сбрасываем при любом сдвиге часов (scheduled и forced), чтобы
            # кворум до advance не дал лишний forced-день после resume-догона.
            with self.db.transaction():
                if forced and not self._forced_tick_mandate_open(wid):
                    return {
                        "world_id": wid,
                        "realms": [],
                        "digest": None,
                        "chat_id": None,
                        "resumed": False,
                        "incomplete": self.world_tick_incomplete(wid),
                        "forced_skipped": True,
                    }
                self.db.update_world(wid, **world_fields)
                self.db.sync_realms_clock_from_world(wid)
                self.db.clear_world_force_tick_votes(wid)

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
                            forced=forced,
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
            if world.get("pending_minor_key") is None:
                next_minor = roll_minor_event(random.Random())
                self.db.update_world(wid, pending_minor_key=next_minor or "")
            # Страховка: сброс после успешного сдвига в этом вызове.
            # На resume не трогаем - голоса, набранные во время incomplete,
            # остаются мандатом на следующий force после догона.
            if not resuming:
                self.db.clear_world_force_tick_votes(wid)
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
        forced: bool = False,
        advance_clock: bool = True,
    ) -> dict:
        """Тик одной долины. При advance_clock=False часы уже выставлены миром."""
        if advance_clock:
            # Одиночный вызов (админ/тесты) гоняет весь континент.
            world_id = self._world_id_for_realm(realm_id)
            world_result = self.run_world_tick(
                world_id, tick_slot=tick_slot, forced=forced
            )
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

            state = FiefTickState(
                grain=fief["grain"],
                goods=fief["goods"],
                might=fief["might"],
                pending_grain=float(fief["pending_grain"]),
                pending_goods=float(fief["pending_goods"]),
                pending_might=float(fief["pending_might"]),
                actions=fief["actions"],
                hungry=bool(fief["hungry"]),
                tiles=tiles,
                barn_level=self.barn_level(fief["id"]),
                farm_mult=farm_mult,
            )
            out = apply_fief_tick(state)
            self.db.update_fief(
                fief["id"],
                grain=out.grain,
                goods=out.goods,
                might=out.might,
                pending_grain=out.pending_grain,
                pending_goods=out.pending_goods,
                pending_might=out.pending_might,
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
        key = realm.get("active_minor_key")
        if key == "harvest":
            return float(minor_effect("harvest").get("farm_mult") or 1.15)
        if key == "drought":
            return float(minor_effect("drought").get("farm_mult") or 0.55)
        if self._active_cattle_plague(int(realm["id"])) is not None:
            return float(catastrophe_effect("cattle_plague").get("farm_mult") or 0.50)
        return 1.0

    def _active_cattle_plague(self, realm_id: int) -> dict | None:
        for ev in self.db.get_active_events(realm_id, kind="catastrophe"):
            if ev.get("event_key") == "cattle_plague":
                return ev
        return None

    def _resolve_active_minor_events(self, realm_id: int) -> None:
        for ev in self.db.get_active_events(realm_id, kind="minor"):
            self.db.update_event(ev["id"], status="resolved")

    def _apply_instant_minor(self, realm_id: int, key: str) -> None:
        eff = minor_effect(key)
        if key == "rats":
            threshold = int(eff.get("unprot_grain_threshold") or 80)
            loss_frac = float(eff.get("loss_frac") or 0.20)
            for fief in self.db.list_fiefs(realm_id):
                barn = self.barn_level(fief["id"])
                unprot = int(fief["grain"] * (1.0 - B.barn_protect_frac(barn)))
                if unprot > threshold:
                    loss = max(1, int(unprot * loss_frac))
                    self.db.update_fief(fief["id"], grain=max(0, fief["grain"] - loss))
            return
        if key == "blight":
            frac = float(eff.get("goods_loss_frac") or 0.18)
            for fief in self.db.list_fiefs(realm_id):
                loss = max(1, int(int(fief["goods"]) * frac)) if int(fief["goods"]) > 0 else 0
                if loss:
                    self.db.update_fief(fief["id"], goods=max(0, int(fief["goods"]) - loss))
            return
        if key == "spoilage":
            frac = float(eff.get("grain_loss_frac") or 0.15)
            for fief in self.db.list_fiefs(realm_id):
                loss = max(1, int(int(fief["grain"]) * frac)) if int(fief["grain"]) > 0 else 0
                if loss:
                    self.db.update_fief(fief["id"], grain=max(0, int(fief["grain"]) - loss))
            return
        if key == "toll":
            flat = int(eff.get("goods_flat_loss") or 12)
            for fief in self.db.list_fiefs(realm_id):
                self.db.update_fief(fief["id"], goods=max(0, int(fief["goods"]) - flat))
            return
        if key == "press_gang":
            loss = int(eff.get("might_loss") or 3)
            for fief in self.db.list_fiefs(realm_id):
                self.db.update_fief(fief["id"], might=max(0, int(fief["might"]) - loss))
            return
        if key == "fire":
            for fief in self.db.list_fiefs(realm_id):
                tiles = [
                    t
                    for t in self.db.fief_tiles(fief["id"])
                    if t.get("building")
                    and t.get("building") != B.BLD_MANOR
                    and not t.get("is_overgrown")
                    and not t.get("damaged")
                ]
                if not tiles:
                    continue
                victim = random.choice(tiles)
                self.db.update_tile(victim["id"], damaged=True)
            return

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
