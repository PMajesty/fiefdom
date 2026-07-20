"""Личка: /start, меню текстом, простая FSM для ввода."""
from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import InlineKeyboardMarkup, Message

from app import balance as B
from app.domain.map_geometry import adjacent_claimable
from app.domain.travel_supply import (
    format_travel_supply_charge_line,
    format_travel_supply_confirm_line,
)

from app.engine import raid_pact_lock_message
from app.handlers.shared import (
    fief_home_kb,
    fief_raid_pact_state,
    format_pact_create_announce,
    get_engine,
    map_realms_kb,
    map_view_kb,
    parse_start_payload,
    post_continent_public,
    post_realm_public,
    reply_game,
    reply_guide,
    reply_map_photo,
    resolve_fief_for_user,
)
from app.messaging import answer_html
from app.domain.resource_bags import stash_amount
from app.domain.resource_registry import tradeable_keys
from app.domain.resource_format import resource_name_ru
from app.presenters.transfer import (
    transfer_confirm_step,
    transfer_custom_amount_step,
    transfer_entry,
    transfer_resource_step,
)
from app.ui.flows import (
    claim_offer,
    pact_menu_offer,
    raid_targets_offer,
)
from app.ui.keyboards import (
    building_types_kb,
    build_tiles_kb,
    caravan_cancel_intent_kb,
    claimable_kb,
    demolish_tiles_kb,
    format_build_cost_label,
    format_build_tile_button,
    format_building_type_label,
    format_claim_button,
    gather_resources_kb,
    pact_invite_kb,
    pact_kb,
    patrol_confirm_callback,
    patrol_confirm_kb,
    pending_cancel_callback,
    pending_cancel_kb,
    cover_confirm_kb,
    raid_cancel_intent_kb,
    raid_confirm_kb,
    raid_targets_kb as raid_targets_kb_plain,
    realm_picker_kb as realm_picker_kb_plain,
    starter_tiles_kb,
)
from app.ui.pending import (
    KIND_SEND_AMOUNT,
    KIND_SEND_CONFIRM,
    KIND_SEND_PICK,
    KIND_SEND_RESOURCE,
    KIND_SEND_TARGET,
    pending_store,
)

logger = logging.getLogger(__name__)

router = Router(name="dm")
router.message.filter(F.chat.type == ChatType.PRIVATE)

# Переходный alias: тесты и старые импорты патчат dm.pending_actions.
pending_actions = pending_store._actions

_MENU_WORDS = {
    "статус": "status",
    "status": "status",
    "карта": "map",
    "map": "map",
    "рынок": "send",
    "market": "send",
    "сделка": "send",
    "trade": "send",
    "караван": "send",
    "обоз": "send",
    "caravan": "send",
    "земля": "claim",
    "занять": "claim",
    "расширить": "claim",
    "клейм": "claim",
    "claim": "claim",
    "строить": "build",
    "стройка": "build",
    "build": "build",
    "дозор": "patrol",
    "patrol": "patrol",
    "набег": "raid",
    "raid": "raid",
    "передать": "send",
    "отдать": "send",
    "дар": "send",
    "send": "send",
    "gift": "send",
    "пакт": "pact",
    "pact": "pact",
    "устав": "guide",
    "гайд": "guide",
    "правила": "guide",
    "guide": "guide",
    "слухи": "rumors",
    "слух": "rumors",
    "сплетни": "rumors",
    "rumors": "rumors",
    "rumor": "rumors",
    "владения": "holdings",
    "владение": "holdings",
    "здания": "holdings",
    "клетки": "holdings",
    "holdings": "holdings",
    "меню": "menu",
    "menu": "menu",
}


def clear_pending(user_id: int) -> None:
    pending_store.clear(user_id)


def set_pending(user_id: int, data: dict) -> None:
    pending_store.set(user_id, data)


def get_pending(user_id: int) -> dict | None:
    return pending_store.get(user_id)


def is_pending_cancel_text(text: str) -> bool:
    return text.strip().lower() in {"отмена", "cancel"}


async def _notify_raid_parties(bot, result) -> None:
    """DM жертве (и перехватчику); блок бота - только warning."""
    targets: list[tuple[int, str]] = [
        (int(result.victim_user_id), result.victim_dm_text()),
    ]
    interceptor_text = result.interceptor_dm_text()
    if (
        interceptor_text
        and result.interceptor_user_id is not None
        and int(result.interceptor_user_id) != int(result.victim_user_id)
    ):
        targets.append((int(result.interceptor_user_id), interceptor_text))

    for chat_id, text in targets:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.warning("raid DM failed chat_id=%s", chat_id, exc_info=True)


def patrol_confirm_text() -> str:
    cost = int(B.PATROL_COST_MIGHT)
    cost_bit = f"−{cost} силы, " if cost > 0 else ""
    return (
        f"Выставить дозор? Усилит защиту от набегов на {B.PATROL_TICKS} тик(а) "
        f"({cost_bit}1 действие, +{B.PATROL_DEFENSE_BONUS} защиты)."
    )


def patrol_prompt_callback(fief_id: int) -> str:
    return f"pat:{int(fief_id)}"


def realm_picker_kb(fiefs: list[dict], engine) -> InlineKeyboardMarkup:
    """Адаптер: подписи усадеб через Engine, разметка в ui.keyboards."""
    entries: list[tuple[int, str]] = []
    for f in fiefs:
        realm = engine.get_realm(f["realm_id"])
        title = realm["title"] if realm else f"#{f['realm_id']}"
        entries.append((int(f["id"]), f"{engine.fief_label(f)} ({title})"))
    return realm_picker_kb_plain(entries)


def raid_target_rows(others: list[dict], engine=None) -> list[dict]:
    """Подписи целей набега: id/label/might для ui.keyboards.raid_targets_kb."""
    targets: list[dict] = []
    for o in others[:20]:
        label = engine.fief_label(o) if engine is not None else o["name"]
        if o.get("via_portal") and engine is not None:
            realm = engine.get_realm(o["realm_id"]) or {}
            title = str(realm.get("title") or "долина")[:12]
            label = f"{title}: {label}"
        targets.append(
            {"id": o["id"], "label": label, "might": o.get("might") or 0}
        )
    return targets


def raid_targets_kb(
    fief_id: int, others: list[dict], engine=None
) -> InlineKeyboardMarkup:
    """Адаптер: подписи целей через Engine (или name), разметка в ui.keyboards."""
    return raid_targets_kb_plain(fief_id, raid_target_rows(others, engine))


def claim_offer_data(engine, fief: dict) -> tuple[list[tuple[int, int]], dict, int]:
    """claimable coords, tile_meta, next_tile_count для claim_offer."""
    views = engine.tile_views(fief["realm_id"])
    owned = {
        (t.x, t.y)
        for t in views
        if t.owner_fief_id == fief["id"] and not t.is_overgrown
    }
    by_xy = {(t.x, t.y): t for t in views}
    realm = engine.get_realm(fief["realm_id"])
    claimable = sorted(
        adjacent_claimable(
            owned,
            by_xy,
            width=realm["width"],
            height=realm["height"],
            for_fief_id=fief["id"],
        )
    )
    tile_meta = {
        (x, y): (by_xy[(x, y)].tile_type, by_xy[(x, y)].is_overgrown)
        for x, y in claimable
        if (x, y) in by_xy
    }
    return claimable, tile_meta, len(owned) + 1


def claim_prompt_text(
    engine,
    fief: dict,
    next_tile_count: int,
    tile_meta: dict[tuple[int, int], tuple[str, bool]],
    *,
    base: str,
) -> str:
    """Промпт занятия: базовый текст + предупреждение, если склад мал."""
    barn = engine.barn_level(int(fief["id"]))
    costs: list[int] = []
    for tile_type, is_overgrown in tile_meta.values():
        is_wilds = (not is_overgrown) and tile_type == B.TILE_WILDS
        costs.append(B.claim_cost(next_tile_count, is_wilds=is_wilds))
    if not costs:
        try:
            costs.append(B.claim_cost(next_tile_count))
        except ValueError:
            return base
    hint = B.claim_stash_gate_message(max(costs), barn)
    if hint:
        return f"{base}\n{hint}"
    return base


async def show_status(message: Message, fief_id: int) -> None:
    engine = get_engine()
    text = engine.status_card(fief_id)
    await reply_game(message, text, reply_markup=fief_home_kb(engine, fief_id))


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    engine = get_engine()
    user = message.from_user
    try:
        engine.ensure_user(user)
        kind, rid = parse_start_payload(command.args)

        if kind == "join" and rid is not None:
            realm = engine.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            existing = engine.fief_of_user_in_realm(user.id, rid)
            if existing:
                engine.remember_last_realm(user.id, rid)
                await show_status(message, existing["id"])
                return
            world_id = realm.get("world_id")
            owned = (
                engine.fief_of_user_in_world(user.id, int(world_id))
                if world_id is not None
                else None
            )
            if owned:
                engine.remember_last_realm(user.id, owned["realm_id"])
                await answer_html(
                    message,
                    "У вас уже есть усадьба на континенте. "
                    "Вторая недоступна - открываю вашу.",
                )
                await show_status(message, owned["id"])
                return
            tiles = engine.starter_tile_choices(rid, 3)
            if not tiles:
                await answer_html(message, "Нет свободных стартовых клеток.")
                return
            await answer_html(
                message,
                f"Выберите стартовую клетку в долине \"{realm['title']}\":",
                reply_markup=starter_tiles_kb(rid, tiles),
            )
            return

        if kind == "realm" and rid is not None:
            realm = engine.get_realm(rid)
            if not realm:
                await answer_html(message, "Долина не найдена.")
                return
            fief = engine.fief_of_user_in_realm(user.id, rid)
            if fief:
                engine.remember_last_realm(user.id, rid)
                await show_status(message, fief["id"])
                return
            world_id = realm.get("world_id")
            owned = (
                engine.fief_of_user_in_world(user.id, int(world_id))
                if world_id is not None
                else None
            )
            if owned:
                engine.remember_last_realm(user.id, owned["realm_id"])
                await answer_html(
                    message,
                    "У вас уже есть усадьба на континенте. "
                    "Вторая недоступна - открываю вашу.",
                )
                await show_status(message, owned["id"])
                return
            tiles = engine.starter_tile_choices(rid, 3)
            if not tiles:
                await answer_html(message, "Нет свободных стартовых клеток.")
                return
            await answer_html(
                message,
                f"Усадьбы ещё нет. Выберите клетку в \"{realm['title']}\":",
                reply_markup=starter_tiles_kb(rid, tiles),
            )
            return

        fiefs = engine.fiefs_of_user(user.id)
        if not fiefs:
            await answer_html(
                message,
                "У вас пока нет усадьбы.\n"
                "В групповом чате долины нажмите \"Моё владение\" или /вч_я.",
            )
            return
        if len(fiefs) > 1:
            await answer_html(
                message,
                "Выберите усадьбу:",
                reply_markup=realm_picker_kb(fiefs, engine),
            )
            return
        engine.remember_last_realm(user.id, fiefs[0]["realm_id"])
        await show_status(message, fiefs[0]["id"])
    except ValueError as exc:
        await answer_html(message, str(exc))
    except Exception:
        logger.exception("cmd_start")
        await answer_html(message, "Ошибка /start.")


# В личке также: /вч_гайд через общий текст устава
@router.message(Command("вч_гайд", "вч_устав", "vch_guide", "vch_rules", "гайд", "устав"))
async def cmd_dm_guide(message: Message) -> None:
    engine = get_engine()
    fief = resolve_fief_for_user(engine, message.from_user.id)
    kb = fief_home_kb(engine, fief["id"]) if fief else None
    await reply_guide(message, engine.guide_text(), reply_markup=kb)


@router.message(Command("меню", "menu"))
async def cmd_menu(message: Message) -> None:
    engine = get_engine()
    fief = resolve_fief_for_user(engine, message.from_user.id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    await show_status(message, fief["id"])


@router.message(F.text, ~F.text.startswith("/"))
async def dm_text(message: Message) -> None:
    """FSM ввода + текстовое меню (slash-команды не перехватываем)."""
    if not message.text:
        return
    user_id = message.from_user.id
    text = message.text.strip()
    engine = get_engine()

    pending = get_pending(user_id)
    if pending:
        try:
            handled = await _handle_pending(message, engine, pending, text)
            if handled:
                return
        except ValueError as exc:
            clear_pending(user_id)
            await answer_html(message, str(exc))
            return
        except Exception:
            logger.exception("pending action")
            clear_pending(user_id)
            await answer_html(message, "Действие сорвалось.")
            return

    key = _MENU_WORDS.get(text.lower())
    if not key:
        await answer_html(
            message,
            "Команды: статус, карта, земля, строить, дозор, набег, "
            "караван, передать, пакт, слухи, владения, устав, меню.",
        )
        return

    fief = resolve_fief_for_user(engine, user_id)
    if not fief:
        await answer_html(message, "Усадьба не найдена. /start")
        return
    fid = fief["id"]

    try:
        if key == "menu":
            await show_status(message, fid)
        elif key == "status":
            await show_status(message, fid)
        elif key == "map":
            realm = engine.get_realm(fief["realm_id"])
            world_id = realm.get("world_id") if realm else None
            if world_id is None:
                await reply_map_photo(
                    message,
                    engine,
                    engine.map_photo(fief["realm_id"], highlight_fief_id=fid),
                    reply_markup=map_view_kb(fid),
                )
            else:
                realms = engine.realms_of_world(int(world_id))
                await reply_game(
                    message,
                    "Карты долин континента - выберите долину:",
                    reply_markup=map_realms_kb(
                        fid, realms, home_realm_id=int(fief["realm_id"])
                    ),
                )
        elif key == "guide":
            await reply_guide(
                message,
                engine.guide_text(),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "rumors":
            await reply_game(
                message,
                engine.rumors_text(fief["realm_id"]),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "holdings":
            await reply_game(
                message,
                engine.holdings_text(fid),
                reply_markup=fief_home_kb(engine, fid),
            )
        elif key == "claim":
            await _offer_claim(message, engine, fief)
        elif key == "build":
            tiles, cost_mult = engine.build_options(fid)
            await answer_html(
                message,
                "Выберите здание:",
                reply_markup=building_types_kb(fid, tiles, cost_mult=cost_mult),
            )
        elif key == "patrol":
            await answer_html(
                message,
                patrol_confirm_text(),
                reply_markup=patrol_confirm_kb(fid),
            )
        elif key == "raid":
            await _offer_raid(message, engine, fief)
        elif key == "send":
            await _offer_send(message, engine, fief)
        elif key == "pact":
            await _offer_pact(message, engine, fief)
    except ValueError as exc:
        await answer_html(message, str(exc), reply_markup=fief_home_kb(engine, fid))
    except Exception:
        logger.exception("dm_text menu")
        await answer_html(message, "Ошибка команды.")


async def _offer_send(message: Message, engine, fief: dict) -> None:
    set_pending(
        message.from_user.id,
        {
            "kind": KIND_SEND_PICK,
            "fief_id": fief["id"],
            "realm_id": fief["realm_id"],
        },
    )
    text, kb = transfer_entry(engine, fief)
    await reply_game(message, text, reply_markup=kb)


async def _offer_claim(message: Message, engine, fief: dict) -> None:
    claimable, tile_meta, next_tile_count = claim_offer_data(engine, fief)
    text, kb = claim_offer(
        int(fief["id"]),
        claimable,
        next_tile_count=next_tile_count,
        tile_meta=tile_meta,
        empty_text="Нет соседних клеток для занятия.",
        prompt_text=claim_prompt_text(
            engine,
            fief,
            next_tile_count,
            tile_meta,
            base="Выберите клетку для занятия:",
        ),
    )
    await answer_html(message, text, reply_markup=kb)


async def _offer_raid(message: Message, engine, fief: dict) -> None:
    open_, _hint = fief_raid_pact_state(engine, fief)
    if not open_:
        realm = engine.get_realm(fief["realm_id"])
        day_number = int(realm["day_number"]) if realm else 1
        await answer_html(
            message,
            raid_pact_lock_message(
                onboard_step=int(fief.get("onboard_step") or 0),
                day_number=day_number,
            ),
            reply_markup=fief_home_kb(engine, fief["id"]),
        )
        return
    others = engine.list_raid_target_fiefs(int(fief["id"]))
    text, kb = raid_targets_offer(
        int(fief["id"]),
        raid_target_rows(others, engine),
        empty_text="Некого грабить.",
        prompt_text=(
            "Выберите цель набега (любая долина континента).\n"
            "Точная сила скрыта - смотрите слухи или спрашивайте. "
            "Защита цели - дружина на месте, сторожка, дозор и перехват пакта."
        ),
    )
    await answer_html(message, text, reply_markup=kb)


async def _offer_pact(message: Message, engine, fief: dict) -> None:
    open_, _hint = fief_raid_pact_state(engine, fief)
    if not open_:
        realm = engine.get_realm(fief["realm_id"])
        day_number = int(realm["day_number"]) if realm else 1
        await answer_html(
            message,
            raid_pact_lock_message(
                onboard_step=int(fief.get("onboard_step") or 0),
                day_number=day_number,
            ),
            reply_markup=fief_home_kb(engine, fief["id"]),
        )
        return
    in_pact = bool(fief.get("pact_id"))
    is_founder = False
    if in_pact:
        pact = engine.get_pact(fief["pact_id"])
        is_founder = bool(pact and pact["founder_fief_id"] == fief["id"])
        name = pact["name"] if pact else "?"
        menu_text = f"Пакт \"{name}\"."
    else:
        menu_text = "Вы не в пакте."
    text, kb = pact_menu_offer(
        int(fief["id"]),
        in_pact=in_pact,
        is_founder=is_founder,
        text=menu_text,
    )
    await answer_html(message, text, reply_markup=kb)


async def _handle_pending(message: Message, engine, pending: dict, text: str) -> bool:
    kind = pending.get("kind")
    user_id = message.from_user.id

    if is_pending_cancel_text(text):
        clear_pending(user_id)
        fid = pending.get("fief_id")
        if fid is not None:
            await show_status(message, int(fid))
        else:
            await answer_html(message, "Отменено.")
        return True

    if kind == "raid_might":
        might = int(text.strip())
        fief_id = int(pending["fief_id"])
        victim_id = int(pending["victim_id"])
        fief = engine.fief_by_id(fief_id) or {}
        men_home = max(0, int(fief.get("might") or 0) - might)
        set_pending(
            user_id,
            {
                "kind": "raid_confirm",
                "fief_id": fief_id,
                "victim_id": victim_id,
                "might": might,
                "open_truce": False,
            },
        )
        vic = engine.fief_by_id(victim_id)
        vic_name = engine.fief_label(vic) if vic else str(victim_id)
        world = engine.world(
            engine.world_id_for_realm(int(fief["realm_id"]))
        )
        lock_text = engine.format_raid_deadline(world or {}, midpoint=True)
        resolve_text = engine.format_raid_deadline(world or {}, midpoint=False)
        truce_note = ""
        if not fief.get("pact_id"):
            truce_note = (
                "\nМожно включить открытое перемирие с другими одиночками "
                "на ту же цель."
            )
        elif fief.get("pact_id"):
            truce_note = "\nСоюзники по пакту сольются в один удар на дороге."
        await reply_game(
            message,
            (
                f"Подтвердите набег на {vic_name}.\n"
                f"Уйдёт {might} силы, дома останется {men_home}.\n"
                f"{format_travel_supply_confirm_line(might)}\n"
                f"Отмена заявки до {lock_text}; бой около {resolve_text}."
                f"{truce_note}"
            ),
            reply_markup=raid_confirm_kb(
                fief_id, show_truce=not bool(fief.get("pact_id"))
            ),
        )
        return True

    if kind == "raid_confirm":
        # Подтверждение только кнопками; текст - подсказка.
        await reply_game(
            message,
            "Нажмите \"Подтвердить\" или \"Отмена\" под сообщением выше.",
            reply_markup=raid_confirm_kb(
                int(pending["fief_id"]),
                show_truce=not bool(
                    (engine.fief_by_id(int(pending["fief_id"])) or {}).get("pact_id")
                ),
            ),
        )
        return True

    if kind in {KIND_SEND_PICK, KIND_SEND_TARGET}:
        target = engine.resolve_target_fief(pending["realm_id"], text)
        if not target:
            await answer_html(
                message,
                "Усадьба не найдена. Id, имя или @username.\n"
                "Или напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        if int(target["id"]) == int(pending["fief_id"]):
            await answer_html(
                message,
                "Нельзя отправить себе. Укажите другую усадьбу.\n"
                "Или напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        set_pending(
            user_id,
            {
                "kind": KIND_SEND_RESOURCE,
                "fief_id": pending["fief_id"],
                "realm_id": pending["realm_id"],
                "target_fief_id": target["id"],
            },
        )
        engine.collect_for_fief(int(pending["fief_id"]))
        sender = engine.fief_by_id(int(pending["fief_id"])) or {}
        text_out, kb = transfer_resource_step(
            engine, sender, int(target["id"])
        )
        await reply_game(message, text_out, reply_markup=kb)
        return True

    if kind == KIND_SEND_AMOUNT:
        res = str(pending.get("res") or "")
        fief_id = int(pending["fief_id"])
        target_id = int(pending["target_fief_id"])
        engine.collect_for_fief(fief_id)
        sender = engine.fief_by_id(fief_id) or {}
        have = stash_amount(sender, res) if res else 0
        amt: int | None = None
        if res and pending.get("custom"):
            try:
                amt = int(text.strip())
            except ValueError:
                amt = None
        if amt is None:
            parsed = _parse_send_line(text)
            if parsed:
                parsed_res, parsed_amt = parsed
                if res and parsed_res != res:
                    await reply_game(
                        message,
                        f"Сейчас выбран ресурс: {resource_name_ru(res)}. "
                        "Напишите число или нажмите Отмена.",
                        reply_markup=pending_cancel_kb(fief_id),
                    )
                    return True
                res = parsed_res
                amt = parsed_amt
            else:
                try:
                    amt = int(text.strip())
                except ValueError:
                    amt = None
        if not res or res not in tradeable_keys() or amt is None or amt <= 0:
            await reply_game(
                message,
                "Напишите число больше 0.\nИли нажмите Отмена.",
                reply_markup=pending_cancel_kb(fief_id),
            )
            return True
        have = stash_amount(sender, res)
        if amt > have:
            text_out, kb = transfer_custom_amount_step(
                fief_id, res=res, have=have
            )
            await reply_game(
                message,
                f"Недостаточно (есть {have}).\n{text_out}",
                reply_markup=kb,
            )
            return True
        set_pending(
            user_id,
            {
                "kind": KIND_SEND_CONFIRM,
                "fief_id": fief_id,
                "realm_id": pending["realm_id"],
                "target_fief_id": target_id,
                "res": res,
                "amt": amt,
            },
        )
        text_out, kb = transfer_confirm_step(
            engine,
            sender,
            target_fief_id=target_id,
            res=res,
            amt=amt,
        )
        await reply_game(message, text_out, reply_markup=kb)
        return True

    if kind == KIND_SEND_CONFIRM:
        from app.ui.keyboards.transfer import transfer_confirm_kb

        await reply_game(
            message,
            "Нажмите \"Отправить\" или \"Отмена\" под сообщением выше.",
            reply_markup=transfer_confirm_kb(int(pending["fief_id"])),
        )
        return True

    if kind == KIND_SEND_RESOURCE:
        fief_id = int(pending["fief_id"])
        target_id = int(pending["target_fief_id"])
        engine.collect_for_fief(fief_id)
        sender = engine.fief_by_id(fief_id) or {}
        text_out, kb = transfer_resource_step(engine, sender, target_id)
        await reply_game(
            message,
            "Выберите ресурс кнопками.\n" + text_out,
            reply_markup=kb,
        )
        return True

    if kind == "cover_budget":
        try:
            budget = int(text.strip())
        except ValueError:
            await answer_html(
                message,
                (
                    f"Нужно число силы от {B.COVER_BUDGET_MIN} "
                    "(потолка нет, лишь сколько есть).\n"
                    "Или напишите \"отмена\"."
                ),
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        if budget < int(B.COVER_BUDGET_MIN):
            await answer_html(
                message,
                (
                    f"Минимум {B.COVER_BUDGET_MIN} силы.\n"
                    "Или напишите \"отмена\"."
                ),
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        mode = str(pending.get("mode") or "any")
        target_id = pending.get("target_fief_id")
        fief_id = int(pending["fief_id"])
        set_pending(
            user_id,
            {
                "kind": "cover_confirm",
                "fief_id": fief_id,
                "mode": mode,
                "budget": int(budget),
                "target_fief_id": target_id,
            },
        )
        fief = engine.fief_by_id(fief_id) or {}
        prior_budget, prior_supply = engine.open_cover_stance_escrow_preview(
            fief_id
        )
        men_home = max(
            0, int(fief.get("might") or 0) + prior_budget - int(budget)
        )
        new_fee = B.travel_supply_grain(int(budget))
        supply_line = format_travel_supply_charge_line(
            new_fee=new_fee, prior_fee=prior_supply
        )
        label = "любого союзника"
        if mode == "specific" and target_id is not None:
            tgt = engine.fief_by_id(int(target_id))
            label = engine.fief_label(tgt) if tgt else str(target_id)
        await reply_game(
            message,
            (
                f"Подтвердите заставу ({label}).\n"
                f"Уйдёт {budget} силы, дома останется {men_home}.\n"
                f"{supply_line}\n"
                "Или напишите \"отмена\"."
            ),
            reply_markup=cover_confirm_kb(fief_id),
        )
        return True

    if kind == "cover_confirm":
        await reply_game(
            message,
            "Нажмите \"Подтвердить\" или \"Отмена\" под сообщением выше.",
            reply_markup=cover_confirm_kb(int(pending["fief_id"])),
        )
        return True

    if kind == "pact_name":
        fief = engine.fief_by_id(pending["fief_id"])
        pact_name = text.strip()[:40]
        msg = engine.create_pact(pending["fief_id"], text)
        clear_pending(user_id)
        await reply_game(
            message, msg, reply_markup=fief_home_kb(engine, pending["fief_id"])
        )
        if fief and pact_name:
            engine.ensure_user(message.from_user)
            await post_realm_public(
                message.bot,
                fief["realm_id"],
                format_pact_create_announce(engine.fief_label(fief), pact_name),
            )
        return True

    if kind == "pact_invite":
        # id усадьбы или имя
        target = engine.resolve_target_fief(pending["realm_id"], text)
        if not target:
            await answer_html(
                message,
                "Усадьба не найдена. Укажите id или имя.\nИли напишите \"отмена\".",
                reply_markup=pending_cancel_kb(pending["fief_id"]),
            )
            return True
        founder = engine.fief_by_id(pending["fief_id"])
        pact = (
            engine.get_pact(founder["pact_id"])
            if founder and founder.get("pact_id")
            else None
        )
        invite = engine.invite_to_pact(pending["fief_id"], target["id"])
        clear_pending(user_id)
        await reply_game(
            message,
            f"Приглашение отправлено: {engine.fief_label(target)}.",
            reply_markup=fief_home_kb(engine, pending["fief_id"]),
        )
        if founder and pact:
            try:
                await message.bot.send_message(
                    int(target["user_id"]),
                    f"Вас приглашают в пакт \"{pact['name']}\" "
                    f"(от {engine.fief_label(founder)}).",
                    reply_markup=pact_invite_kb(int(target["id"]), int(invite["id"])),
                )
            except Exception:
                logger.warning(
                    "send pact invite DM to %s failed", target.get("user_id")
                )
        return True

    return False


def _tradeable_res_map() -> dict[str, str]:
    from app.domain.resource_format import synonym_to_key

    return synonym_to_key(tradeable_only=True)


def _send_re() -> re.Pattern[str]:
    from app.domain.resource_format import tradeable_synonym_alternatives

    alt = tradeable_synonym_alternatives()

    return re.compile(rf"^({alt})\s+(\d+)$", re.IGNORECASE)


def _parse_send_line(text: str) -> tuple[str, int] | None:
    m = _send_re().match(text.strip())
    if not m:
        return None
    return _tradeable_res_map()[m.group(1).lower()], int(m.group(2))

