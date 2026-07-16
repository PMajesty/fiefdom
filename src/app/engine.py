"""Игровой движок: операции над долиной через БД + доменную логику."""
from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app import balance as B
from app.config import TICK_HOUR, TICK_MINUTE, TIMEZONE
from app.database import Database
from app.domain import absence as absence_mod
from app.domain.digest import format_decree, format_digest
from app.domain.economy import (
    TileView,
    adjacent_claimable,
    fief_daily_production,
    render_map,
)
from app.domain.events import (
    CATASTROPHES,
    MINOR_EVENTS,
    next_catastrophe_delay_days,
    pick_catastrophe,
    roll_minor_event,
)
from app.balance import best_rectangle
from app.domain.map_gen import GenTile, append_strip, coord_label, generate_map
from app.domain.raids import resolve_raid
from app.domain.tick import FiefTickState, apply_fief_tick, collect_pending

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Engine:
    def __init__(self, db: Database):
        self.db = db

    # ---------- realm ----------
    def create_realm(self, chat_id: int, title: str, creator_user_id: int) -> tuple[dict, str]:
        existing = self.db.get_realm_by_chat(chat_id)
        if existing:
            raise ValueError("В этом чате долина уже основана. Используйте /вч_карта")

        width, height = best_rectangle(B.MAP_MIN_TILES)
        tiles = generate_map(width, height)
        tz = TIMEZONE
        delay = next_catastrophe_delay_days(random.Random())
        next_cat = _utcnow() + timedelta(days=delay)
        realm = self.db.create_realm(
            chat_id=chat_id,
            title=title or "Долина",
            width=width,
            height=height,
            timezone=tz,
            tick_hour=TICK_HOUR,
            tick_minute=TICK_MINUTE,
            feature_flags=dict(B.DEFAULT_FEATURE_FLAGS),
            next_catastrophe_at=next_cat,
        )
        # первый тик — завтра (не сразу после основания днём)
        from zoneinfo import ZoneInfo

        local_today = datetime.now(ZoneInfo(tz)).date()
        self.db.update_realm(realm["id"], last_tick_local_date=local_today)
        realm = self.db.get_realm(realm["id"])
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
        msg = (
            f"🏰 Вотчина основана: <b>{realm['title']}</b>\n"
            f"Карта {width}×{height}. Тик каждый день в {TICK_HOUR:02d}:{TICK_MINUTE:02d} ({tz}).\n"
            f"Напишите боту в личку или нажмите «Моё владение», чтобы получить усадьбу."
        )
        return realm, msg

    def begin_wipe(self, realm_id: int) -> str:
        code = secrets.token_hex(3).upper()
        self.db.update_realm(
            realm_id,
            wipe_confirm_code=code,
            wipe_confirm_until=_utcnow() + timedelta(minutes=10),
        )
        realm = self.db.get_realm(realm_id)
        return (
            f"⚠️ Удаление долины «{realm['title']}» (id={realm_id}).\n"
            f"Чтобы подтвердить, отправьте:\n"
            f"<code>/вч_wipe {realm_id} {code} УДАЛИТЬ</code>\n"
            f"Код действует 10 минут."
        )

    def confirm_wipe(self, realm_id: int, code: str, confirm_word: str) -> str:
        realm = self.db.get_realm(realm_id)
        if not realm:
            raise ValueError("Долина не найдена")
        if confirm_word != "УДАЛИТЬ":
            raise ValueError("Нужно слово УДАЛИТЬ")
        until = realm.get("wipe_confirm_until")
        if not realm.get("wipe_confirm_code") or not until or until < _utcnow():
            raise ValueError("Нет активного кода. Сначала /вч_wipe_start")
        if code.upper() != str(realm["wipe_confirm_code"]).upper():
            raise ValueError("Неверный код")
        self.db.delete_realm(realm_id)
        return f"Долина id={realm_id} стёрта."

    # ---------- join / onboarding ----------
    def ensure_user(self, user) -> None:
        name = (user.full_name or user.first_name or "Путник").strip()
        self.db.upsert_user(user.id, user.username, name)

    def starter_tile_choices(self, realm_id: int, count: int = 3) -> list[dict]:
        tiles = self.db.get_tiles(realm_id)
        fiefs = self.db.list_fiefs(realm_id)
        owned = {(t["x"], t["y"]) for t in tiles if t["owner_fief_id"]}
        occupied_owners = []
        for t in tiles:
            if t["owner_fief_id"]:
                occupied_owners.append((t["x"], t["y"]))

        candidates = [
            t
            for t in tiles
            if t["owner_fief_id"] is None
            and t["tile_type"] not in (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER)
            and not t.get("is_overgrown")
        ]

        def min_dist(t):
            if not occupied_owners:
                return 99
            return min(abs(t["x"] - ox) + abs(t["y"] - oy) for ox, oy in occupied_owners)

        candidates.sort(key=lambda t: (-min_dist(t), t["y"], t["x"]))
        # разнесённые
        picked: list[dict] = []
        for t in candidates:
            if all(abs(t["x"] - p["x"]) + abs(t["y"] - p["y"]) >= 2 for p in picked):
                picked.append(t)
            if len(picked) >= count:
                break
        if len(picked) < count:
            for t in candidates:
                if t not in picked:
                    picked.append(t)
                if len(picked) >= count:
                    break
        return picked[:count]

    def join_fief(self, realm_id: int, user, tile_id: int) -> tuple[dict, str]:
        self.ensure_user(user)
        existing = self.db.get_fief_by_user(realm_id, user.id)
        if existing:
            raise ValueError("У вас уже есть усадьба в этой долине")

        tile = self.db._fetchone("SELECT * FROM map_tiles WHERE id=%s AND realm_id=%s;", (tile_id, realm_id))
        if not tile or tile["owner_fief_id"] is not None:
            raise ValueError("Клетка недоступна")
        if tile["tile_type"] in (B.TILE_WILDS, B.TILE_ROAD, B.TILE_RIVER):
            raise ValueError("Нельзя начать здесь")

        display = (user.full_name or user.first_name or "Путник").strip()
        name = f"Усадьба {display.split()[0]}"
        fief = self.db.create_fief(
            realm_id,
            user.id,
            name,
            grain=B.STARTING_GRAIN,
            goods=B.STARTING_GOODS,
            might=B.STARTING_MIGHT,
            actions=1,
            onboard_step=1,
        )
        self.db.update_tile(
            tile["id"],
            owner_fief_id=fief["id"],
            building=B.BLD_FARM,
            building_level=B.STARTING_FARM_LEVEL,
            is_core=True,
        )
        self.db.set_last_realm(user.id, realm_id)
        self.maybe_grow_map(realm_id)
        return fief, (
            f"🏡 {name} основана на {coord_label(tile['x'], tile['y'])} "
            f"({B.TILE_NAMES_RU[tile['tile_type']]}).\n"
            f"Стартовый набор: ферма I, {B.STARTING_GRAIN} зерна, "
            f"{B.STARTING_GOODS} товаров, {B.STARTING_MIGHT} силы.\n"
            f"Откройте статус — урожай собирается автоматически."
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
        return fief_daily_production(views, hungry=bool(fief["hungry"]), farm_mult=farm_mult)

    def collect_for_fief(self, fief_id: int) -> list[str]:
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
        )
        return notes

    def status_card(self, fief_id: int) -> str:
        notes = self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        realm = self.db.get_realm(fief["realm_id"])
        tiles = self.db.fief_tiles(fief_id)
        prod = self.fief_prod(fief)
        barn = self.barn_level(fief_id)
        flags = []
        if fief["hungry"]:
            flags.append("Голод")
        if fief.get("patrol_until") and fief["patrol_until"] > _utcnow():
            flags.append("Дозор")
        if fief.get("shield_until") and fief["shield_until"] > _utcnow():
            flags.append("Щит")
        inactive_days = (_utcnow() - fief["last_active_at"]).days if fief.get("last_active_at") else 0
        tier = absence_mod.inactivity_tier(inactive_days)
        if tier == "dormant":
            flags.append("Дремлет")
        flag_s = (", ".join(flags)) if flags else "—"
        militia = B.militia_upkeep_grain(fief["might"])
        land = B.land_upkeep(len([t for t in tiles if not t.get("is_overgrown")]))
        lines = [
            f"🏡 <b>{fief['name']}</b> — день {realm['day_number']}",
            f"Клеток: {len(tiles)}/{B.TILE_HARD_CAP} · Действий: {fief['actions']}/{B.ACTIONS_BANK_MAX}",
            f"Зерно: {fief['grain']} · Товары: {fief['goods']} · Сила: {fief['might']}",
            f"Склад: до {B.stash_cap(barn)} (амбар {barn or 'нет'})",
            f"Производство/день: +{prod.grain:.0f} зерна, +{prod.goods:.0f} товаров, +{prod.might:.0f} силы",
            f"Содержание: земля {land} зерна, дружина {militia} зерна",
            f"Статусы: {flag_s}",
        ]
        if notes:
            lines.append("· " + " · ".join(notes))
        if fief["onboard_step"] == 2:
            lines.append("Квест: постройте или улучшите здание (+15 товаров).")
        elif fief["onboard_step"] == 3:
            lines.append("Квест: создайте или примите сделку на рынке (+10 зерна).")
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
            legend[fid] = f"{f['name']}{tag}"
        claimable = None
        if highlight_fief_id:
            owned = {
                (t.x, t.y)
                for t in views
                if t.owner_fief_id == highlight_fief_id and not t.is_overgrown
            }
            claimable = adjacent_claimable(
                owned, {(t.x, t.y): t for t in views}, for_fief_id=highlight_fief_id
            )
        grid = render_map(
            realm["width"],
            realm["height"],
            views,
            legend,
            highlight_fief_id=highlight_fief_id,
            claimable=claimable,
        )
        return f"🗺️ {realm['title']} (день {realm['day_number']})\n<pre>{grid}</pre>"

    # ---------- actions ----------
    def _spend_action(self, fief: dict) -> None:
        if fief["actions"] < 1:
            raise ValueError("Нет действий на сегодня (макс. запас 3)")
        if fief["frozen"]:
            raise ValueError("Усадьба заморожена")
        self.db.update_fief(fief["id"], actions=fief["actions"] - 1, last_active_at=_utcnow())

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
        views = {(t.x, t.y): t for t in self.tile_views(realm_id)}
        owned = {(t["x"], t["y"]) for t in tiles if not t.get("is_overgrown")}
        if (x, y) not in adjacent_claimable(owned, views, for_fief_id=fief_id):
            raise ValueError("Клетка не соседняя")

        is_wilds = target["tile_type"] == B.TILE_WILDS
        if target.get("is_overgrown"):
            prev = target.get("owner_fief_id")
            cost = B.claim_cost(n, is_wilds=False)
            if fief["goods"] < cost:
                raise ValueError(f"Нужно {cost} товаров")
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
            return f"Занята заросшая клетка {coord_label(x, y)} (−{cost} товаров)."

        cost = B.claim_cost(n, is_wilds=is_wilds)
        if fief["goods"] < cost:
            raise ValueError(f"Нужно {cost} товаров (у вас {fief['goods']})")

        self._spend_action(fief)
        fief = self.db.get_fief(fief_id)
        self.db.update_fief(fief_id, goods=fief["goods"] - cost)

        new_type = target["tile_type"]
        ruins_loot = 0
        if is_wilds:
            new_type = random.choice(B.WILDS_CLEAR_TO)
        if new_type == B.TILE_RUINS and not target.get("ruins_looted"):
            ruins_loot = random.randint(B.RUINS_LOOT_MIN, B.RUINS_LOOT_MAX)
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
        # вторая клетка — тоже core
        if n == 2:
            for t in self.db.fief_tiles(fief_id):
                self.db.update_tile(t["id"], is_core=True)

        self.maybe_grow_map(realm_id)
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
            raise ValueError("Сначала расчистите глушь (клейм)")

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

        if current and current != building:
            raise ValueError("На клетке уже другое здание (снос только катастрофами)")
        if not current:
            target_level = 1
        else:
            target_level = level + 1
        if target_level > 3:
            raise ValueError("Максимальный уровень")
        # если damaged сбросили выше; апгрейд
        cost = B.building_upgrade_cost(building, target_level)
        realm = self.db.get_realm(fief["realm_id"])
        if realm.get("active_minor_key") == "good_stone" and realm.get("active_minor_until") and realm["active_minor_until"] > _utcnow():
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

    def _onboard_build(self, fief_id: int) -> None:
        fief = self.db.get_fief(fief_id)
        if fief["onboard_step"] == 2:
            self.db.update_fief(
                fief_id,
                onboard_step=3,
                goods=fief["goods"] + B.ONBOARD_DAY2_GOODS,
            )

    def patrol(self, fief_id: int) -> str:
        fief = self.db.get_fief(fief_id)
        if fief["might"] < B.PATROL_COST_MIGHT:
            raise ValueError(f"Нужно {B.PATROL_COST_MIGHT} силы")
        self._spend_action(fief)
        fief = self.db.get_fief(fief_id)
        self.db.update_fief(
            fief_id,
            might=fief["might"] - B.PATROL_COST_MIGHT,
            patrol_until=_utcnow() + timedelta(hours=B.PATROL_HOURS),
        )
        return f"Дозор выставлен на {B.PATROL_HOURS}ч (−{B.PATROL_COST_MIGHT} силы)."

    def raid(self, attacker_id: int, victim_id: int, might: int) -> str:
        if might < B.RAID_MIN_MIGHT:
            raise ValueError(f"Минимум {B.RAID_MIN_MIGHT} силы")
        atk = self.db.get_fief(attacker_id)
        vic = self.db.get_fief(victim_id)
        if not atk or not vic or atk["realm_id"] != vic["realm_id"]:
            raise ValueError("Цель не найдена")
        if atk["id"] == vic["id"]:
            raise ValueError("Нельзя грабить себя")
        if atk["hungry"]:
            raise ValueError("Голодные мужики не воюют")
        if atk["might"] < might:
            raise ValueError("Недостаточно силы")
        now = _utcnow()
        if vic.get("shield_until") and vic["shield_until"] > now:
            raise ValueError("У жертвы щит после набега")
        if atk.get("last_raid_at") and atk["last_raid_at"] + timedelta(hours=B.RAID_ATTACKER_COOLDOWN_HOURS) > now:
            raise ValueError("Ещё рано для нового набега")
        last_pair = self.db.last_raid_attacker_victim(attacker_id, victim_id)
        if last_pair and last_pair + timedelta(hours=B.RAID_SAME_VICTIM_HOURS) > now:
            raise ValueError("Кулдаун на эту жертву")

        self.collect_for_fief(attacker_id)
        self.collect_for_fief(victim_id)
        atk = self.db.get_fief(attacker_id)
        vic = self.db.get_fief(victim_id)

        realm = self.db.get_realm(atk["realm_id"])
        fog = (
            realm.get("active_minor_key") == "fog"
            and realm.get("active_minor_until")
            and realm["active_minor_until"] > now
        )
        watch_def = self.fief_prod(vic).defense
        patrol = bool(vic.get("patrol_until") and vic["patrol_until"] > now)
        intercept = False
        interceptor = None
        if vic.get("pact_id"):
            for m in self.db.pact_members(vic["pact_id"]):
                if m["id"] == vic["id"]:
                    continue
                if not m.get("cover_allies"):
                    continue
                if m["might"] >= B.INTERCEPT_MIGHT:
                    intercept = True
                    interceptor = m
                    break

        result = resolve_raid(
            attacker_name=atk["name"],
            victim_name=vic["name"],
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

        self._spend_action(atk)
        atk = self.db.get_fief(attacker_id)
        self.db.update_fief(
            attacker_id,
            might=atk["might"] - result.might_lost,
            last_raid_at=now,
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
                shield_until=now + timedelta(hours=B.RAID_VICTIM_SHIELD_HOURS),
            )
            # атакующий получает лут с капом
            barn = self.barn_level(attacker_id)
            cap = B.stash_cap(barn)
            g_add = min(result.grain_stolen, max(0, cap - atk["grain"]))
            d_add = min(result.goods_stolen, max(0, cap - atk["goods"]))
            self.db.update_fief(attacker_id, grain=atk["grain"] + g_add, goods=atk["goods"] + d_add)

        self.db.log_raid(
            realm_id=atk["realm_id"],
            attacker_fief_id=attacker_id,
            victim_fief_id=victim_id,
            success=result.success,
            might_spent=might,
            grain_stolen=result.grain_stolen,
            goods_stolen=result.goods_stolen,
            public_line=result.public_line,
        )
        # буфер сводки
        lines = list(realm.get("pending_raid_lines") or [])
        lines.append(result.public_line)
        self.db.update_realm(realm["id"], pending_raid_lines=lines)
        return result.public_line

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
        self.collect_for_fief(fief_id)
        fief = self.db.get_fief(fief_id)
        have = fief["grain"] if give_res == B.RES_GRAIN else fief["goods"]
        if have < give_amt:
            raise ValueError("Недостаточно ресурса для предложения")
        # эскроу
        if give_res == B.RES_GRAIN:
            self.db.update_fief(fief_id, grain=fief["grain"] - give_amt)
        else:
            self.db.update_fief(fief_id, goods=fief["goods"] - give_amt)
        offer = self.db.create_trade(
            realm_id=fief["realm_id"],
            offerer_fief_id=fief_id,
            target_fief_id=target_fief_id,
            give_res=give_res,
            give_amt=give_amt,
            want_res=want_res,
            want_amt=want_amt,
            expires_at=_utcnow() + timedelta(hours=B.TRADE_EXPIRE_HOURS),
        )
        return f"Лот #{offer['id']}: отдаю {give_amt} {B.RES_NAMES_RU[give_res]} за {want_amt} {B.RES_NAMES_RU[want_res]}."

    def accept_trade(self, fief_id: int, trade_id: int) -> str:
        trade = self.db.get_trade(trade_id)
        if not trade or trade["status"] != "open":
            raise ValueError("Лот недоступен")
        if trade["expires_at"] < _utcnow():
            self._refund_trade(trade)
            raise ValueError("Лот истёк")
        if trade["offerer_fief_id"] == fief_id:
            raise ValueError("Свой лот")
        if trade.get("target_fief_id") and trade["target_fief_id"] != fief_id:
            raise ValueError("Лот адресован другому")
        buyer = self.db.get_fief(fief_id)
        seller = self.db.get_fief(trade["offerer_fief_id"])
        if buyer["realm_id"] != trade["realm_id"]:
            raise ValueError("Другая долина")
        self.collect_for_fief(fief_id)
        buyer = self.db.get_fief(fief_id)
        want = trade["want_res"]
        want_amt = trade["want_amt"]
        have = buyer["grain"] if want == B.RES_GRAIN else buyer["goods"]
        if have < want_amt:
            raise ValueError("Недостаточно ресурса для оплаты")

        realm = self.db.get_realm(buyer["realm_id"])
        bonus = 0.0
        if (
            realm.get("active_minor_key") == "fair"
            and realm.get("active_minor_until")
            and realm["active_minor_until"] > _utcnow()
        ):
            bonus = 0.05
        wedding = (
            realm.get("active_minor_key") == "wedding"
            and realm.get("active_minor_until")
            and realm["active_minor_until"] > _utcnow()
        )

        if want == B.RES_GRAIN:
            self.db.update_fief(fief_id, grain=buyer["grain"] - want_amt)
        else:
            self.db.update_fief(fief_id, goods=buyer["goods"] - want_amt)

        give_amt = int(trade["give_amt"])
        pay_bonus = int(want_amt * bonus)
        recv_bonus = int(give_amt * bonus)

        seller = self.db.get_fief(trade["offerer_fief_id"])
        if want == B.RES_GRAIN:
            self.db.update_fief(seller["id"], grain=seller["grain"] + want_amt + pay_bonus)
        else:
            self.db.update_fief(seller["id"], goods=seller["goods"] + want_amt + pay_bonus)

        buyer = self.db.get_fief(fief_id)
        cap = B.stash_cap(self.barn_level(fief_id))
        if trade["give_res"] == B.RES_GRAIN:
            add = min(give_amt + recv_bonus, max(0, cap - buyer["grain"]))
            self.db.update_fief(fief_id, grain=buyer["grain"] + add)
        else:
            add = min(give_amt + recv_bonus, max(0, cap - buyer["goods"]))
            self.db.update_fief(fief_id, goods=buyer["goods"] + add)

        if wedding:
            for fid in (fief_id, trade["offerer_fief_id"]):
                f = self.db.get_fief(fid)
                self.db.update_fief(fid, grain=f["grain"] + 8)

        self.db.update_trade(trade_id, status="done")
        for fid in (fief_id, trade["offerer_fief_id"]):
            f = self.db.get_fief(fid)
            if f["onboard_step"] == 3:
                self.db.update_fief(fid, onboard_step=4, grain=f["grain"] + B.ONBOARD_DAY3_GRAIN)
        return f"Сделка #{trade_id} закрыта."

    def cancel_trade(self, fief_id: int, trade_id: int) -> str:
        trade = self.db.get_trade(trade_id)
        if not trade or trade["offerer_fief_id"] != fief_id or trade["status"] != "open":
            raise ValueError("Нельзя отменить")
        self._refund_trade(trade)
        return f"Лот #{trade_id} отменён, ресурс возвращён."

    def _refund_trade(self, trade: dict) -> None:
        if trade["status"] != "open":
            return
        seller = self.db.get_fief(trade["offerer_fief_id"])
        if seller:
            if trade["give_res"] == B.RES_GRAIN:
                self.db.update_fief(seller["id"], grain=seller["grain"] + trade["give_amt"])
            else:
                self.db.update_fief(seller["id"], goods=seller["goods"] + trade["give_amt"])
        self.db.update_trade(trade["id"], status="cancelled")

    def market_text(self, realm_id: int, fief_id: int | None = None) -> str:
        offers = self.db.list_open_trades(realm_id, fief_id)
        if not offers:
            return "🛒 Рынок пуст."
        lines = ["🛒 Рынок:"]
        for o in offers:
            tgt = f" → только вам" if o.get("target_fief_id") else ""
            lines.append(
                f"#{o['id']}: {o['give_amt']} {B.RES_NAMES_RU[o['give_res']]} "
                f"→ {o['want_amt']} {B.RES_NAMES_RU[o['want_res']]}{tgt}"
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
        return f"Пакт «{pact['name']}» создан. Приглашайте союзников."

    def invite_to_pact(self, founder_fief_id: int, target_fief_id: int) -> str:
        founder = self.db.get_fief(founder_fief_id)
        target = self.db.get_fief(target_fief_id)
        if not founder.get("pact_id"):
            raise ValueError("Сначала создайте пакт")
        pact = self.db.get_pact(founder["pact_id"])
        if pact["founder_fief_id"] != founder_fief_id:
            raise ValueError("Приглашает только основатель")
        members = self.db.pact_members(pact["id"])
        if len(members) >= B.PACT_SIZE_MAX:
            raise ValueError("Пакт полон")
        if target.get("pact_id"):
            raise ValueError("Цель уже в пакте")
        if target["realm_id"] != founder["realm_id"]:
            raise ValueError("Другая долина")
        self.db.update_fief(target_fief_id, pact_id=pact["id"])
        return f"{target['name']} в пакте «{pact['name']}»."

    def leave_pact(self, fief_id: int) -> str:
        fief = self.db.get_fief(fief_id)
        if not fief.get("pact_id"):
            raise ValueError("Вы не в пакте")
        pact_id = fief["pact_id"]
        pact = self.db.get_pact(pact_id)
        self.db.update_fief(fief_id, pact_id=None)
        members = self.db.pact_members(pact_id)
        if len(members) < B.PACT_SIZE_MIN:
            self.db.dissolve_pact(pact_id)
            return "Вы вышли. Пакт распущен (меньше 2 участников)."
        if pact and pact["founder_fief_id"] == fief_id and members:
            self.db._update("pacts", pact_id, {"founder_fief_id": members[0]["id"]})
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
        now = _utcnow()
        for fief in self.db.list_fiefs(realm_id):
            days = (now - fief["last_active_at"]).days if fief.get("last_active_at") else 0
            tier = absence_mod.inactivity_tier(days)
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

    # ---------- daily tick ----------
    def run_realm_tick(self, realm_id: int) -> dict:
        realm = self.db.get_realm(realm_id)
        self.apply_absence(realm_id)
        # expire trades
        for t in self.db.list_open_trades(realm_id):
            if t["expires_at"] < _utcnow():
                self._refund_trade(t)

        farm_mult = 1.0
        if realm.get("active_minor_key") == "harvest" and realm.get("active_minor_until") and realm["active_minor_until"] > _utcnow():
            farm_mult = 1.25
        if realm.get("active_minor_key") == "drought" and realm.get("active_minor_until") and realm["active_minor_until"] > _utcnow():
            farm_mult = 0.70

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
            # expire patrol
            if fief.get("patrol_until") and fief["patrol_until"] < _utcnow():
                self.db.update_fief(fief["id"], patrol_until=None)

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

        # minor event
        minor_key = roll_minor_event(random.Random())
        event_line = None
        deserter_event = None
        if minor_key:
            meta = MINOR_EVENTS[minor_key]
            narrative = meta["canned_narrative"]
            self.db.update_realm(
                realm_id,
                active_minor_key=minor_key,
                active_minor_until=_utcnow() + timedelta(hours=24),
            )
            event_line = f"{meta['name_ru']} — {meta['mechanics']}"
            self._apply_instant_minor(realm_id, minor_key)
            if minor_key == "deserter":
                deserter_event = self.db.create_event(
                    realm_id=realm_id,
                    kind="minor",
                    event_key="deserter",
                    payload={},
                    narrative=narrative,
                    status="active",
                    resolves_at=_utcnow() + timedelta(hours=24),
                )
            else:
                self.db.create_event(
                    realm_id=realm_id,
                    kind="minor",
                    event_key=minor_key,
                    payload={},
                    narrative=narrative,
                    status="resolved",
                    resolves_at=_utcnow() + timedelta(hours=24),
                )
        else:
            self.db.update_realm(realm_id, active_minor_key=None, active_minor_until=None)
            event_line = None

        # feuds
        feud_lines = self._feud_lines(realm_id)
        raid_lines = list(realm.get("pending_raid_lines") or [])
        self.db.update_realm(realm_id, pending_raid_lines=[])

        # market summary
        offers = self.db.list_open_trades(realm_id)
        market_line = f"{len(offers)} лот." if offers else None
        if offers:
            best = max(offers, key=lambda o: o["give_amt"] + o["want_amt"])
            market_line = (
                f"{len(offers)} лот. Лучший: {best['give_amt']} "
                f"{B.RES_NAMES_RU[best['give_res']]} за {best['want_amt']} "
                f"{B.RES_NAMES_RU[best['want_res']]}"
            )

        day = realm["day_number"] + 1
        tz = ZoneInfo(realm.get("timezone") or TIMEZONE)
        local_date = datetime.now(tz).date()
        self.db.update_realm(
            realm_id,
            day_number=day,
            last_tick_at=_utcnow(),
            last_tick_local_date=local_date,
        )

        sunday_extra = None
        if local_date.weekday() == 6:
            sunday_extra = self._sunday_extra(realm_id)

        grow_msg = self.maybe_grow_map(realm_id)
        digest = format_digest(
            realm_title=realm["title"],
            day=day,
            night_lines=raid_lines,
            event_line=event_line,
            market_line=market_line,
            feud_lines=feud_lines,
            sunday_extra=sunday_extra,
        )
        if grow_msg:
            digest += f"\n📜 {grow_msg}"

        return {
            "digest": digest,
            "deserter_event": deserter_event,
            "chat_id": realm["chat_id"],
            "outcomes": outcomes,
        }

    def _apply_instant_minor(self, realm_id: int, key: str) -> None:
        if key == "rats":
            for fief in self.db.list_fiefs(realm_id):
                barn = self.barn_level(fief["id"])
                unprot = int(fief["grain"] * (1.0 - B.barn_protect_frac(barn)))
                if unprot > 150:
                    loss = max(1, int(unprot * 0.10))
                    self.db.update_fief(fief["id"], grain=max(0, fief["grain"] - loss))

    def _feud_lines(self, realm_id: int) -> list[str]:
        since = _utcnow() - timedelta(days=B.FEUD_WINDOW_DAYS)
        raids = self.db.raids_since(realm_id, since)
        counts: dict[tuple[int, int], int] = {}
        for r in raids:
            key = (r["attacker_fief_id"], r["victim_fief_id"])
            counts[key] = counts.get(key, 0) + 1
        lines = []
        for (a, v), c in counts.items():
            if c >= B.FEUD_RAIDS_IN_DAYS:
                af = self.db.get_fief(a)
                vf = self.db.get_fief(v)
                if af and vf:
                    lines.append(f"Вражда: {af['name']} против {vf['name']}")
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
        return f"Титулы: больше всех земель — {top['name']}."

    def help_text(self) -> str:
        return (
            "🏰 <b>Вотчина</b> — средневековая долина в чате.\n\n"
            "Группа:\n"
            "/вотчина — основать долину\n"
            "/вч_карта /vch_map — карта\n"
            "/вч_рынок /vch_market — рынок\n"
            "/вч_сводка /vch_digest — последняя логика дня\n"
            "/вч_помощь /vch_help — эта справка\n"
            "/вч_я /vch_me — личка с усадьбой\n\n"
            "Личка: статус, стройка, клейм, набеги, пакты, сделки.\n"
            "1 действие в день (запас до 3). Тик в 13:00 МСК."
        )
