"""Общие хелперы хендлеров: движок, админ, deep-link, realm/fief."""
from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from app import balance as B
from app.config import ADMIN_USER_ID
from app.domain.cta import choose_primary_cta
from app.rendering.map_image import MapPhoto
from app.engine import (
    Engine,
    raid_pact_lock_hint,
    raid_pact_unlocked,
)
from app.messaging import (
    answer_html,
    answer_photo_bytes,
    escape_html,
    reply_guide_document,
)
from app.notifier import (
    announce_continent,
    announce_realm,
    bot_username_or_none,
    deep_link_url,
    open_estate_kb,
    post_continent_public,
    post_digest,
    post_realm_public,
)
from app.ui.keyboards import (
    estate_hub_kb,
    home_kb,
    main_menu_kb,
    map_realms_kb,
    map_view_kb,
    more_menu_kb,
    prepared_intents_kb as prepared_intents_kb_plain,
    valley_hub_kb,
)
from app.wiring import get_engine

logger = logging.getLogger(__name__)

_START_REALM = re.compile(r"^realm_(\d+)$", re.IGNORECASE)
_START_JOIN = re.compile(r"^join_(\d+)$", re.IGNORECASE)


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
    return engine.resolve_realm_for_user(user_id, chat)


def resolve_fief_for_user(
    engine: Engine,
    user_id: int,
    realm_id: int | None = None,
) -> dict | None:
    return engine.resolve_fief_for_user(user_id, realm_id)


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
    from app.domain.resource_format import resource_name_ru


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


def prepared_intents_kb(engine: Engine, fief_id: int) -> InlineKeyboardMarkup:
    """Кнопки снятия открытых заявок + назад в меню (снимок через Engine)."""
    fid = int(fief_id)
    raids, caravans, covers = engine.list_prepared_intents(fid)
    raid_cancels: list[tuple[int, str]] = []
    for intent in raids:
        if intent.get("status") != "open":
            continue
        raid_cancels.append(
            (int(intent["id"]), engine.raid_intent_target_label(intent))
        )
    caravan_cancels: list[tuple[int, str]] = []
    for intent in caravans:
        if intent.get("status") != "open":
            continue
        caravan_cancels.append(
            (int(intent["id"]), engine.caravan_intent_target_label(intent))
        )
    cover_cancels: list[tuple[int, str]] = []
    for intent in covers:
        if intent.get("status") != "open":
            continue
        cover_cancels.append(
            (int(intent["id"]), engine.cover_intent_stance_label(intent))
        )
    return prepared_intents_kb_plain(
        fid,
        raid_cancels=raid_cancels,
        caravan_cancels=caravan_cancels,
        cover_cancels=cover_cancels,
    )


def fief_home_kb(engine: Engine, fief_id: int) -> InlineKeyboardMarkup:
    """Дом с CTA по актуальному снимку усадьбы из БД."""
    fief = engine.fief_by_id(fief_id)
    if not fief:
        return main_menu_kb(fief_id)
    active = engine.demolish_options(fief_id)
    n = len(active)
    realm = engine.get_realm(fief["realm_id"])
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
        prepared_count=engine.prepared_intents_count(fief_id),
    )


def fief_raid_pact_state(engine: Engine, fief: dict) -> tuple[bool, str | None]:
    """(открыто?, хвост подписи замка) по усадьбе и дню долины."""
    realm = engine.get_realm(fief["realm_id"])
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
