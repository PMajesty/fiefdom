"""Общие хелперы хендлеров: движок, админ, deep-link, realm/fief."""
from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app import balance as B
from app.config import ADMIN_USER_ID
from app.database import get_db
from app.engine import (
    Engine,
    raid_pact_lock_hint,
    raid_pact_unlocked,
)
from app.domain.map_image import MapPhoto
from app.messaging import (
    answer_html,
    answer_photo_bytes,
    escape_html,
    reply_guide_document,
    send_html,
)

logger = logging.getLogger(__name__)

_engine: Engine | None = None

_START_REALM = re.compile(r"^realm_(\d+)$", re.IGNORECASE)
_START_JOIN = re.compile(r"^join_(\d+)$", re.IGNORECASE)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_db())
    return _engine


def realm_upgrade_cost_mult(engine: Engine, realm: dict | None) -> float:
    """Множитель стоимости стройки/апгрейда (минор + активные катастрофы)."""
    if not realm:
        return 1.0
    return engine.realm_modifiers(realm).upgrade_cost_mult()


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
    """Realm из группового чата, last_realm с усадьбой пользователя или единственной усадьбы."""
    db = engine.db
    if chat is not None:
        chat_type = getattr(chat, "type", None)
        chat_id = getattr(chat, "id", None)
        if chat_type in ("group", "supergroup") and chat_id is not None:
            return db.get_realm_by_chat(chat_id)

    user = db.get_user(user_id)
    last_realm_id = user.get("last_realm_id") if user else None
    if last_realm_id:
        realm = db.get_realm(last_realm_id)
        if realm and db.get_fief_by_user(int(realm["id"]), user_id):
            return realm

    fiefs = db.list_fiefs_by_user(user_id)
    if len(fiefs) == 1:
        owned_realm_id = int(fiefs[0]["realm_id"])
        if last_realm_id is not None and int(last_realm_id) != owned_realm_id:
            db.set_last_realm(user_id, owned_realm_id)
        return db.get_realm(owned_realm_id)
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
        fief = db.get_fief_by_user(realm["id"], user_id)
        if fief:
            return fief
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


def format_send_announce(
    sender_name: str,
    receiver_name: str,
    amt: int,
    res: str,
) -> str:
    from app.domain.resources import resource_name_ru

    res_name = resource_name_ru(res)
    return (
        f"📦 {escape_html(sender_name)} отправила обоз: "
        f"{int(amt)} {res_name} → {escape_html(receiver_name)}"
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


async def post_realm_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> bool:
    """Короткое объявление в групповой чат долины. Ошибки не роняют хендлер."""
    if not text or not realm_id:
        return False
    try:
        engine = get_engine()
        realm = engine.db.get_realm(int(realm_id))
        if not realm:
            return False
        chat_id = realm.get("chat_id")
        if chat_id is None:
            return False
        return bool(
            await send_game(bot, int(chat_id), text, reply_markup=reply_markup)
        )
    except Exception:
        logger.warning(
            "post_realm_public failed realm_id=%s", realm_id, exc_info=True
        )
        return False


async def announce_realm(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Короткое объявление владельцам усадеб долины в личку. Ошибки не роняют хендлер."""
    if not text:
        return
    try:
        engine = get_engine()
        for fief in engine.db.list_fiefs(int(realm_id)):
            uid = fief.get("user_id")
            if not uid:
                continue
            try:
                await send_game(
                    bot, int(uid), text, reply_markup=reply_markup
                )
            except Exception:
                logger.warning(
                    "announce_realm dm failed user=%s realm_id=%s",
                    uid,
                    realm_id,
                    exc_info=True,
                )
    except Exception:
        logger.warning("announce_realm failed realm_id=%s", realm_id, exc_info=True)


async def announce_continent(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Объявление всем усадьбам континента (своя долина и остальные долины мира)."""
    if not text:
        return
    seen: set[int] = set()
    try:
        engine = get_engine()
        targets = [int(realm_id)]
        for nb in engine.db.list_adjacent_realms(int(realm_id)):
            targets.append(int(nb["id"]))
        for rid in targets:
            if rid in seen:
                continue
            seen.add(rid)
            await announce_realm(bot, rid, text, reply_markup=reply_markup)
    except Exception:
        logger.warning(
            "announce_continent failed realm_id=%s", realm_id, exc_info=True
        )


async def post_continent_public(
    bot, realm_id: int, text: str, *, reply_markup=None
) -> None:
    """Крупный обоз: в групповые чаты всех долин континента."""
    if not text:
        return
    seen: set[int] = set()
    try:
        engine = get_engine()
        targets = [int(realm_id)]
        for nb in engine.db.list_adjacent_realms(int(realm_id)):
            targets.append(int(nb["id"]))
        for rid in targets:
            if rid in seen:
                continue
            seen.add(rid)
            await post_realm_public(bot, rid, text, reply_markup=reply_markup)
    except Exception:
        logger.warning(
            "post_continent_public failed realm_id=%s", realm_id, exc_info=True
        )


def map_realms_kb(
    fief_id: int,
    realms: list[dict],
    *,
    home_realm_id: int | None = None,
) -> InlineKeyboardMarkup:
    """Выбор долины для просмотра карты."""
    rows = []
    for r in realms:
        title = str(r.get("title") or f"#{r['id']}")[:28]
        suffix = " · ваша" if home_realm_id and int(r["id"]) == int(home_realm_id) else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{title}{suffix}",
                    callback_data=f"mapr:{int(fief_id)}:{int(r['id'])}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="< Меню", callback_data=f"st:{int(fief_id)}")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def map_view_kb(fief_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Другие долины",
                    callback_data=f"map:{int(fief_id)}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="< Меню",
                    callback_data=f"st:{int(fief_id)}",
                )
            ],
        ]
    )


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
        return "Караван", f"snd:{fid}"
    if actions > 0 and onboard_step == 3:
        if can_build:
            return "Квест: строить", f"bld:{fid}"
        return "Караван", f"snd:{fid}"
    if actions > 0:
        if tile_count < 3:
            return "Занять землю", f"clm:{fid}"
        if can_build:
            return "Строить", f"bld:{fid}"
        if unlocked and might >= 5:
            return "Набег", f"rad:{fid}"
        return "Занять землю", f"clm:{fid}"
    return "Караван", f"snd:{fid}"


def home_kb(
    fief_id: int,
    primary_label: str,
    primary_callback: str,
) -> InlineKeyboardMarkup:
    """Дом: primary CTA + два хаба (Усадьба / Долина) + карта и устав."""
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=primary_label, callback_data=primary_callback)],
        [
            InlineKeyboardButton(
                text="Усадьба (дела)",
                callback_data=f"hub:e:{fid}",
            ),
            InlineKeyboardButton(
                text="Долина (связи)",
                callback_data=f"hub:v:{fid}",
            ),
        ],
        [
            InlineKeyboardButton(text="Карта (мир)", callback_data=f"map:{fid}"),
            InlineKeyboardButton(
                text="Устав (правила)",
                callback_data=f"gd:{fid}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _raid_pact_hub_buttons(
    fief_id: int,
    *,
    raid_pact_open: bool,
    lock_hint: str | None,
    raid_hint: str,
    pact_hint: str,
) -> tuple[InlineKeyboardButton, InlineKeyboardButton]:
    """Кнопки Набег/Пакт: при замке - только хвост lock (без скобок, чтобы влезло)."""
    fid = int(fief_id)
    if raid_pact_open:
        return (
            InlineKeyboardButton(
                text=f"Набег ({raid_hint})",
                callback_data=f"rad:{fid}",
            ),
            InlineKeyboardButton(
                text=f"Пакт ({pact_hint})",
                callback_data=f"pct:{fid}",
            ),
        )
    suffix = lock_hint or "закрыто"
    return (
        InlineKeyboardButton(
            text=f"Набег - {suffix}",
            callback_data=f"lock:rad:{fid}",
        ),
        InlineKeyboardButton(
            text=f"Пакт - {suffix}",
            callback_data=f"lock:pct:{fid}",
        ),
    )


def estate_hub_kb(
    fief_id: int,
    *,
    raid_pact_open: bool = True,
    lock_hint: str | None = None,
) -> InlineKeyboardMarkup:
    """Хабы Усадьба: действия за 1 действие (земля, стройка, сбор, дозор, снос, набег)."""
    fid = int(fief_id)
    raid_btn, _pact = _raid_pact_hub_buttons(
        fid,
        raid_pact_open=raid_pact_open,
        lock_hint=lock_hint,
        raid_hint="атака",
        pact_hint="союз",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Владения (обзор)",
                    callback_data=f"hld:{fid}",
                ),
            ],
            [
                InlineKeyboardButton(text="Земля (клетка)", callback_data=f"clm:{fid}"),
                InlineKeyboardButton(
                    text="Строить (здание)",
                    callback_data=f"bld:{fid}",
                ),
            ],
            [
                InlineKeyboardButton(text="Сбор (добыча)", callback_data=f"gth:{fid}"),
                InlineKeyboardButton(text="Дозор (защита)", callback_data=f"pat:{fid}"),
            ],
            [
                InlineKeyboardButton(text="Снос (вернуть)", callback_data=f"dml:{fid}"),
                raid_btn,
            ],
            [InlineKeyboardButton(text="< Меню", callback_data=f"home:{fid}")],
        ]
    )


def valley_hub_kb(
    fief_id: int,
    *,
    raid_pact_open: bool = True,
    lock_hint: str | None = None,
) -> InlineKeyboardMarkup:
    """Хабы Долина: бесплатные связи (караван, пакт, слухи)."""
    fid = int(fief_id)
    _raid, pact_btn = _raid_pact_hub_buttons(
        fid,
        raid_pact_open=raid_pact_open,
        lock_hint=lock_hint,
        raid_hint="атака",
        pact_hint="союз",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Караван (передача)", callback_data=f"snd:{fid}"
                ),
                pact_btn,
            ],
            [
                InlineKeyboardButton(text="Слухи", callback_data=f"rum:{fid}"),
            ],
            [InlineKeyboardButton(text="< Меню", callback_data=f"home:{fid}")],
        ]
    )


def more_menu_kb(
    fief_id: int,
    *,
    raid_pact_open: bool = True,
    lock_hint: str | None = None,
) -> InlineKeyboardMarkup:
    """Совместимость: старый flat \"Ещё\" свёрнут в выбор хаба.

    Живой callback more: обновляет дом целиком (см. cb_more).
    """
    _ = (raid_pact_open, lock_hint)
    fid = int(fief_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Усадьба (дела)",
                    callback_data=f"hub:e:{fid}",
                ),
                InlineKeyboardButton(
                    text="Долина (связи)",
                    callback_data=f"hub:v:{fid}",
                ),
            ],
            [InlineKeyboardButton(text="< Меню", callback_data=f"home:{fid}")],
        ]
    )


def main_menu_kb(
    fief_id: int,
    fief: dict | None = None,
    tile_count: int = 2,
    *,
    day_number: int = B.RAID_PACT_UNLOCK_DAY,
    min_build_cost: int | None = None,
    next_claim_cost: int | None = None,
) -> InlineKeyboardMarkup:
    """Домашняя клавиатура усадьбы (status-first). Без снимка fief - безопасный CTA."""
    fid = int(fief_id)
    if fief is None:
        return home_kb(fid, "Обновить статус", f"st:{fid}")
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
    return home_kb(fid, label, cb)


def fief_home_kb(engine: Engine, fief_id: int) -> InlineKeyboardMarkup:
    """Дом с CTA по актуальному снимку усадьбы из БД."""
    fief = engine.db.get_fief(fief_id)
    if not fief:
        return main_menu_kb(fief_id)
    tiles = engine.db.fief_tiles(fief_id)
    active = [t for t in tiles if not t.get("is_overgrown")]
    n = len(active)
    realm = engine.db.get_realm(fief["realm_id"])
    day_number = int(realm["day_number"]) if realm else 1
    cost_mult = realm_upgrade_cost_mult(engine, realm)
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


def _photo_file_id_from_message(sent: Message | None) -> str | None:
    if sent is None or not sent.photo:
        return None
    return sent.photo[-1].file_id


async def reply_map_photo(
    message: Message,
    engine: Engine,
    photo: MapPhoto,
    **kwargs: Any,
) -> None:
    """Карта PNG + подпись; кэширует Telegram file_id по отпечатку."""
    sent = await answer_photo_bytes(
        message,
        photo.png_bytes,
        caption=photo.caption,
        file_id=photo.file_id,
        **kwargs,
    )
    if sent is None:
        await answer_html(message, "Не удалось отправить карту.")
        return
    file_id = _photo_file_id_from_message(sent)
    if file_id and (photo.file_id is None or file_id != photo.file_id):
        engine.remember_map_file_id(photo.fingerprint, file_id)
    if photo.caption_extra:
        await reply_game(message, photo.caption_extra)


async def reply_guide(message: Message, text: str, **kwargs: Any) -> None:
    """Устав длиннее лимита Telegram - одним .txt-файлом."""
    await reply_guide_document(message, text, **kwargs)


async def send_game(bot, chat_id: int, text: str, **kwargs: Any) -> bool:
    """Отправка HTML от движка в чат. True, если сообщение ушло."""
    if text is None:
        return False
    plain = str(text)
    if not plain:
        return False
    kwargs.pop("parse_mode", None)
    try:
        await bot.send_message(chat_id, plain, parse_mode=ParseMode.HTML, **kwargs)
        return True
    except TelegramBadRequest as exc:
        logger.warning("send_game: HTML rejected, fallback send_html: %s", exc)
        return await send_html(bot, chat_id, plain, **kwargs)
    except Exception as exc:
        logger.error("send_game failed: %s", exc)
        return False
