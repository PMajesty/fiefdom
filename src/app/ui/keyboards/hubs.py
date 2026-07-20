"""Клавиатуры хабов: дом, усадьба/долина, карта, заявки."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import balance as B
from app.domain.cta import choose_primary_cta


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


def home_kb(
    fief_id: int,
    primary_label: str,
    primary_callback: str,
    *,
    prepared_count: int = 0,
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
    ]
    if int(prepared_count) > 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Заявки ({int(prepared_count)})",
                    callback_data=f"prep:{fid}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Карта (мир)", callback_data=f"map:{fid}"),
            InlineKeyboardButton(
                text="Устав (правила)",
                callback_data=f"gd:{fid}",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_button_label(text: str, max_len: int = 28) -> str:
    plain = str(text or "").strip() or "?"
    if len(plain) <= max_len:
        return plain
    return plain[: max_len - 1] + "..."


def prepared_intents_kb(
    fief_id: int,
    *,
    raid_cancels: list[tuple[int, str]],
    caravan_cancels: list[tuple[int, str]],
    cover_cancels: list[tuple[int, str]] | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки снятия открытых заявок + назад в меню.

    *_cancels: (intent_id, подпись).
    """
    fid = int(fief_id)
    rows: list[list[InlineKeyboardButton]] = []
    for intent_id, target in raid_cancels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Снять набег: {_short_button_label(target)}",
                    callback_data=f"radx:{fid}:{int(intent_id)}",
                )
            ]
        )
    for intent_id, target in caravan_cancels:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Вернуть обоз: {_short_button_label(target)}",
                    callback_data=f"cvx:{fid}:{int(intent_id)}",
                )
            ]
        )
    for intent_id, label in cover_cancels or []:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Снять заставу: {_short_button_label(label)}",
                    callback_data=f"zsx:{fid}:{int(intent_id)}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="< Меню", callback_data=f"home:{fid}")]
    )
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
            [
                InlineKeyboardButton(
                    text="Заявки (набеги/обозы)",
                    callback_data=f"prep:{fid}",
                )
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
    """Хабы Долина: бесплатные связи (караван, пакт, слухи, заявки)."""
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
                InlineKeyboardButton(
                    text="Заявки",
                    callback_data=f"prep:{fid}",
                ),
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
    prepared_count: int = 0,
) -> InlineKeyboardMarkup:
    """Домашняя клавиатура усадьбы (status-first). Без снимка fief - безопасный CTA."""
    fid = int(fief_id)
    if fief is None:
        return home_kb(
            fid, "Обновить статус", f"st:{fid}", prepared_count=prepared_count
        )
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
    return home_kb(fid, label, cb, prepared_count=prepared_count)

