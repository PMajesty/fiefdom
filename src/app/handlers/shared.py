"""Общие хелперы хендлеров: движок, админ, deep-link, realm/fief."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import balance as B
from app.config import ADMIN_USER_ID
from app.database import get_db
from app.domain.events import minor_effect
from app.engine import (
    Engine,
    raid_pact_lock_hint,
    raid_pact_unlocked,
)
from app.messaging import answer_html, escape_html, send_html

logger = logging.getLogger(__name__)

_engine: Engine | None = None

_START_REALM = re.compile(r"^realm_(\d+)$", re.IGNORECASE)
_START_JOIN = re.compile(r"^join_(\d+)$", re.IGNORECASE)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_db())
    return _engine


def realm_upgrade_cost_mult(realm: dict | None, *, now: datetime | None = None) -> float:
    """Множитель стоимости стройки/апгрейда по активному мелкому событию."""
    if not realm:
        return 1.0
    key = realm.get("active_minor_key")
    until = realm.get("active_minor_until")
    if not key or not until:
        return 1.0
    current = now or datetime.now(timezone.utc)
    if until <= current:
        return 1.0
    try:
        return float(minor_effect(key).get("upgrade_cost_mult", 1.0))
    except KeyError:
        return 1.0


def is_admin(user_id: int | None) -> bool:
    if user_id is None or ADMIN_USER_ID is None:
        return False
    return int(user_id) == int(ADMIN_USER_ID)


def parse_start_payload(payload: str | None) -> tuple[str | None, int | None]:
    """Разбор deep-link: realm_{id} | join_{realm_id} → (kind, id)."""
    if not payload:
        return None, None
    text = payload.strip()
    m = _START_JOIN.match(text)
    if m:
        return "join", int(m.group(1))
    m = _START_REALM.match(text)
    if m:
        return "realm", int(m.group(1))
    return None, None


def resolve_realm_for_user(engine: Engine, user_id: int, chat: Any = None) -> dict | None:
    """Realm из группового чата, last_realm пользователя или единственной усадьбы."""
    db = engine.db
    if chat is not None:
        chat_type = getattr(chat, "type", None)
        chat_id = getattr(chat, "id", None)
        if chat_type in ("group", "supergroup") and chat_id is not None:
            return db.get_realm_by_chat(chat_id)

    user = db.get_user(user_id)
    if user and user.get("last_realm_id"):
        realm = db.get_realm(user["last_realm_id"])
        if realm:
            return realm

    fiefs = db.list_fiefs_by_user(user_id)
    if len(fiefs) == 1:
        return db.get_realm(fiefs[0]["realm_id"])
    return None


def resolve_fief_for_user(
    engine: Engine,
    user_id: int,
    realm_id: int | None = None,
) -> dict | None:
    db = engine.db
    if realm_id is not None:
        return db.get_fief_by_user(realm_id, user_id)
    realm = resolve_realm_for_user(engine, user_id)
    if realm:
        return db.get_fief_by_user(realm["id"], user_id)
    fiefs = db.list_fiefs_by_user(user_id)
    if len(fiefs) == 1:
        return fiefs[0]
    return None


def deep_link_url(bot_username: str, payload: str) -> str:
    return f"https://t.me/{bot_username}?start={payload}"


def open_estate_kb(bot_username: str, realm_id: int) -> InlineKeyboardMarkup:
    """Кнопка deep-link в личку: \"Открыть усадьбу\"."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть усадьбу",
                    url=deep_link_url(bot_username, f"realm_{int(realm_id)}"),
                )
            ]
        ]
    )


async def bot_username_or_none(bot) -> str | None:
    try:
        me = await bot.get_me()
        return me.username or None
    except Exception:
        logger.warning("bot_username_or_none: get_me failed", exc_info=True)
        return None


async def post_digest(bot, chat_id: int, realm_id: int, digest: str) -> None:
    """Публикует сводку в группу; кнопка deep-link - если есть username бота."""
    kb = None
    username = await bot_username_or_none(bot)
    if username:
        kb = open_estate_kb(username, realm_id)
    await send_game(bot, chat_id, digest, reply_markup=kb)


def format_join_announce(fief_name: str) -> str:
    return f"🏡 В долине новая усадьба: {escape_html(fief_name)}"


def format_raid_announce(public_line: str) -> str:
    return f"⚔️ {escape_html(public_line)}"


def format_trade_post_announce(
    fief_name: str,
    give_amt: int,
    give_res: str,
    want_amt: int,
    want_res: str,
) -> str:
    give = B.RES_NAMES_RU.get(give_res, give_res)
    want = B.RES_NAMES_RU.get(want_res, want_res)
    return (
        f"🛒 {escape_html(fief_name)} выставляет лот: "
        f"отдаёт {int(give_amt)} {give} за {int(want_amt)} {want}"
    )


def format_trade_accept_announce(
    buyer_name: str,
    seller_name: str,
    give_amt: int,
    give_res: str,
    want_amt: int,
    want_res: str,
) -> str:
    give = B.RES_NAMES_RU.get(give_res, give_res)
    want = B.RES_NAMES_RU.get(want_res, want_res)
    return (
        f"🛒 Сделка: {escape_html(buyer_name)} забрала "
        f"{int(give_amt)} {give} у {escape_html(seller_name)} "
        f"за {int(want_amt)} {want}"
    )


def format_pact_create_announce(fief_name: str, pact_name: str) -> str:
    return (
        f"🤝 Новый пакт \"{escape_html(pact_name)}\" "
        f"({escape_html(fief_name)})"
    )


def format_pact_join_announce(fief_name: str, pact_name: str) -> str:
    return (
        f"🤝 {escape_html(fief_name)} в пакте \"{escape_html(pact_name)}\""
    )


def format_pact_leave_announce(
    fief_name: str,
    pact_name: str,
    *,
    dissolved: bool = False,
) -> str:
    if dissolved:
        return (
            f"🤝 {escape_html(fief_name)} больше не в пакте - "
            f"\"{escape_html(pact_name)}\" распущен"
        )
    return (
        f"🤝 {escape_html(fief_name)} больше не в пакте "
        f"\"{escape_html(pact_name)}\""
    )


async def announce_realm(bot, realm_id: int, text: str) -> None:
    """Короткое объявление в групповой чат долины. Ошибки не роняют хендлер."""
    if not text:
        return
    try:
        realm = get_engine().db.get_realm(int(realm_id))
        chat_id = realm.get("chat_id") if realm else None
        if not chat_id:
            return
        await send_game(bot, int(chat_id), text)
    except Exception:
        logger.warning("announce_realm failed realm_id=%s", realm_id, exc_info=True)


def choose_primary_cta(
    fief_id: int,
    *,
    actions: int,
    onboard_step: int,
    tile_count: int = 2,
    goods: int = 0,
    might: int = 0,
    day_number: int = B.RAID_PACT_UNLOCK_DAY,
    min_build_cost: int | None = None,
    next_claim_cost: int | None = None,
) -> tuple[str, str]:
    """Эвристика \"что делать сейчас\": (подпись кнопки, callback_data).

    Набег в primary CTA только после unlock (квесты + день долины).
    Не предлагает стройку/клейм, если товаров заведомо не хватает.
    """
    fid = int(fief_id)
    actions = int(actions)
    onboard_step = int(onboard_step)
    tile_count = int(tile_count)
    goods = int(goods)
    might = int(might)
    day_number = int(day_number)
    unlocked = raid_pact_unlocked(onboard_step=onboard_step, day_number=day_number)

    if next_claim_cost is None and tile_count < B.TILE_HARD_CAP:
        try:
            next_claim_cost = B.claim_cost(tile_count + 1)
        except ValueError:
            next_claim_cost = None

    can_claim = (
        actions > 0
        and next_claim_cost is not None
        and goods >= int(next_claim_cost)
    )
    if min_build_cost is not None:
        can_build = actions > 0 and goods >= int(min_build_cost)
    else:
        can_build = actions > 0 and goods >= 20

    if actions > 0 and onboard_step == 2:
        if can_claim:
            return "Квест: занять землю", f"clm:{fid}"
        return "Рынок", f"mkt:{fid}"
    if actions > 0 and onboard_step == 3:
        if can_build:
            return "Квест: строить", f"bld:{fid}"
        return "Рынок", f"mkt:{fid}"
    if actions > 0:
        if tile_count < 3:
            return "Занять землю", f"clm:{fid}"
        if can_build:
            return "Строить", f"bld:{fid}"
        if unlocked and might >= 5:
            return "Набег", f"rad:{fid}"
        return "Занять землю", f"clm:{fid}"
    return "Рынок", f"mkt:{fid}"


def home_kb(fief_id: int, primary_label: str, primary_callback: str) -> InlineKeyboardMarkup:
    """Дом: один primary CTA + Статус + свёрнутое \"Ещё\"."""
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=primary_label, callback_data=primary_callback)],
            [
                InlineKeyboardButton(text="Статус", callback_data=f"st:{fid}"),
                InlineKeyboardButton(text="Ещё", callback_data=f"more:{fid}"),
            ],
        ]
    )


def more_menu_kb(
    fief_id: int,
    *,
    drought_mitigate: bool = False,
    raid_pact_open: bool = True,
    lock_hint: str | None = None,
) -> InlineKeyboardMarkup:
    """Полный набор действий (раскрытие \"Ещё\").

    Пока Набег/Пакт закрыты - подписи-замки с callback lock:… (пояснение без трат).
    """
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    if drought_mitigate:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Полив (10 товаров)",
                    callback_data=f"drt:{fid}",
                )
            ]
        )
    if raid_pact_open:
        raid_btn = InlineKeyboardButton(text="Набег", callback_data=f"rad:{fid}")
        pact_btn = InlineKeyboardButton(text="Пакт", callback_data=f"pct:{fid}")
    else:
        suffix = lock_hint or "закрыто"
        raid_btn = InlineKeyboardButton(
            text=f"Набег - {suffix}",
            callback_data=f"lock:rad:{fid}",
        )
        pact_btn = InlineKeyboardButton(
            text=f"Пакт - {suffix}",
            callback_data=f"lock:pct:{fid}",
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(text="Карта", callback_data=f"map:{fid}"),
                InlineKeyboardButton(text="Рынок", callback_data=f"mkt:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Земля", callback_data=f"clm:{fid}"),
                InlineKeyboardButton(text="Строить", callback_data=f"bld:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Дозор", callback_data=f"pat:{fid}"),
                raid_btn,
            ],
            [
                InlineKeyboardButton(text="Сделка", callback_data=f"trd:{fid}"),
                pact_btn,
            ],
            [
                InlineKeyboardButton(text="Устав", callback_data=f"gd:{fid}"),
                InlineKeyboardButton(text="< Назад", callback_data=f"home:{fid}"),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_kb(
    fief_id: int,
    fief: dict | None = None,
    tile_count: int = 2,
    *,
    drought_mitigate: bool = False,
    day_number: int = B.RAID_PACT_UNLOCK_DAY,
    min_build_cost: int | None = None,
    next_claim_cost: int | None = None,
) -> InlineKeyboardMarkup:
    """Домашняя клавиатура усадьбы (status-first). Без снимка fief - безопасный CTA."""
    fid = int(fief_id)
    if fief is None:
        kb = home_kb(fid, "Обновить статус", f"st:{fid}")
    else:
        label, cb = choose_primary_cta(
            fid,
            actions=int(fief.get("actions") or 0),
            onboard_step=int(fief.get("onboard_step") or 0),
            tile_count=tile_count,
            goods=int(fief.get("goods") or 0),
            might=int(fief.get("might") or 0),
            day_number=day_number,
            min_build_cost=min_build_cost,
            next_claim_cost=next_claim_cost,
        )
        kb = home_kb(fid, label, cb)
    if not drought_mitigate:
        return kb
    rows = [list(row) for row in kb.inline_keyboard]
    rows.insert(
        1,
        [
            InlineKeyboardButton(
                text="Полив (10 товаров)",
                callback_data=f"drt:{fid}",
            )
        ],
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fief_home_kb(engine: Engine, fief_id: int) -> InlineKeyboardMarkup:
    """Дом с CTA по актуальному снимку усадьбы из БД."""
    fief = engine.db.get_fief(fief_id)
    can_water = False
    try:
        can_water = engine.fief_can_mitigate_drought(fief_id)
    except Exception:
        can_water = False
    if not fief:
        return main_menu_kb(fief_id, drought_mitigate=can_water)
    tiles = engine.db.fief_tiles(fief_id)
    active = [t for t in tiles if not t.get("is_overgrown")]
    n = len(active)
    realm = engine.db.get_realm(fief["realm_id"])
    day_number = int(realm["day_number"]) if realm else 1
    cost_mult = realm_upgrade_cost_mult(realm)
    min_build = B.min_any_build_action_cost(active, cost_mult=cost_mult)
    next_claim = None
    if n < B.TILE_HARD_CAP:
        try:
            next_claim = B.claim_cost(n + 1)
        except ValueError:
            next_claim = None
    return main_menu_kb(
        fief_id,
        fief=fief,
        tile_count=n,
        drought_mitigate=can_water,
        day_number=day_number,
        min_build_cost=min_build,
        next_claim_cost=next_claim,
    )


def fief_raid_pact_state(engine: Engine, fief: dict) -> tuple[bool, str | None]:
    """(открыто?, хвост подписи замка) по усадьбе и дню долины."""
    realm = engine.db.get_realm(fief["realm_id"])
    day_number = int(realm["day_number"]) if realm else 1
    step = int(fief.get("onboard_step") or 0)
    open_ = raid_pact_unlocked(onboard_step=step, day_number=day_number)
    hint = None if open_ else raid_pact_lock_hint(onboard_step=step, day_number=day_number)
    return open_, hint


async def reply_game(message: Message, text: str, **kwargs: Any) -> None:
    """Ответ с HTML от движка (теги не экранируем)."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return
    kwargs.pop("parse_mode", None)
    try:
        await message.answer(plain, parse_mode=ParseMode.HTML, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("reply_game: HTML rejected, fallback answer_html: %s", exc)
        await answer_html(message, plain, **kwargs)
    except Exception as exc:
        logger.error("reply_game failed: %s", exc)


async def send_game(bot, chat_id: int, text: str, **kwargs: Any) -> None:
    """Отправка HTML от движка в чат."""
    if text is None:
        return
    plain = str(text)
    if not plain:
        return
    kwargs.pop("parse_mode", None)
    try:
        await bot.send_message(chat_id, plain, parse_mode=ParseMode.HTML, **kwargs)
    except TelegramBadRequest as exc:
        logger.warning("send_game: HTML rejected, fallback send_html: %s", exc)
        await send_html(bot, chat_id, plain, **kwargs)
    except Exception as exc:
        logger.error("send_game failed: %s", exc)
