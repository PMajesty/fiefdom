"""CallbackQuery: меню усадьбы, клейм, стройка, набег, караван, пакт, старт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import balance as B
from app.domain.resource_bags import stash_amount
from app.domain.resource_registry import resource_defs, tradeable_keys
from app.engine import raid_pact_lock_message
from app.handlers import dm as dm_mod
from app.handlers.shared import (
    estate_hub_kb,
    fief_home_kb,
    fief_raid_pact_state,
    format_join_announce,
    format_pact_join_announce,
    format_pact_leave_announce,
    get_engine,
    map_realms_kb,
    map_view_kb,
    post_continent_public,
    post_realm_public,
    prepared_intents_kb,
    reply_game,
    reply_guide,
    reply_map_photo,
    valley_hub_kb,
)
from app.messaging import answer_html, send_game
from app.presenters.transfer import (
    transfer_amount_step,
    transfer_confirm_step,
    transfer_custom_amount_step,
    transfer_entry,
    transfer_find_prompt,
    transfer_resource_step,
)
from app.ui.flows import (
    claim_offer,
    pact_menu_offer,
    raid_targets_offer,
)
from app.ui.keyboards import cover_ally_pick_kb, cover_stance_kb
from app.ui.pending import (
    KIND_SEND_AMOUNT,
    KIND_SEND_CONFIRM,
    KIND_SEND_PICK,
    KIND_SEND_RESOURCE,
    KIND_SEND_TARGET,
)

logger = logging.getLogger(__name__)

router = Router(name="callbacks")


async def _ok(callback: CallbackQuery) -> None:
    try:
        await callback.answer()
    except Exception:
        pass


async def _reply_prepared_intents(
    message,
    engine,
    fief_id: int,
    *,
    prefix: str | None = None,
) -> None:
    """Показать карточку заявок; если пусто после отмены - дом.

    Карточка содержит HTML движка - только через reply_game (не answer_html).
    """
    card = engine.prepared_intents_card(fief_id)
    text = f"{prefix}\n\n{card}" if prefix else card
    if engine.prepared_intents_count(fief_id) > 0:
        await reply_game(
            message,
            text,
            reply_markup=prepared_intents_kb(engine, fief_id),
        )
        return
    if prefix:
        await reply_game(
            message,
            prefix,
            reply_markup=fief_home_kb(engine, fief_id),
        )
        return
    await reply_game(
        message,
        card,
        reply_markup=prepared_intents_kb(engine, fief_id),
    )


@router.callback_query(F.data.startswith("cat:"))
async def cb_catastrophe_contribute(callback: CallbackQuery) -> None:
    """Вклад в катастрофу (группа или личка)."""
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        event_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else "might5"
        if action != "might5":
            await callback.answer("Неизвестное действие", show_alert=True)
            return
        total = engine.contribute_catastrophe_might(
            event_id, callback.from_user.id, amount=5
        )
        await callback.answer(f"Вложено! Всего силы в котле: {total}", show_alert=True)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_catastrophe_contribute")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("gth:"))
async def cb_gather(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        if len(parts) == 2:
            await _ok(callback)
            await answer_html(
                callback.message,
                (
                    "Сбор за 1 действие - плоская добыча, здания не нужны:\n"
                    + "\n".join(
                        f"• {r.synonyms[0]} +{B.gather_amount(r.key)}"
                        for r in resource_defs()
                    )
                ),
                reply_markup=dm_mod.gather_resources_kb(fief_id),
            )
            return
        resource = parts[2]
        msg = engine.gather_resource(fief_id, resource)
        await _ok(callback)
        await reply_game(
            callback.message,
            msg + "\n" + engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_gather")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("dml:"))
async def cb_demolish(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        if len(parts) == 2:
            tiles = engine.demolish_options(fief_id)
            await _ok(callback)
            await answer_html(
                callback.message,
                (
                    f"Снос здания: 1 действие, возврат "
                    f"{int(B.DEMOLISH_REFUND_FRAC * 100)}% вложенных товаров. "
                    "Двор (главная клетка) снести нельзя."
                ),
                reply_markup=dm_mod.demolish_tiles_kb(fief_id, tiles),
            )
            return
        x, y = int(parts[2]), int(parts[3])
        msg = engine.demolish_building(fief_id, x, y)
        await _ok(callback)
        await reply_game(
            callback.message,
            msg + "\n" + engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_demolish")
        await callback.answer("Ошибка", show_alert=True)


async def _finish_starter_pick(callback: CallbackQuery) -> None:
    engine = get_engine()
    _, realm_s, tile_s = callback.data.split(":", 2)
    realm_id = int(realm_s)
    tile_id = int(tile_s)
    fief, msg = engine.join_fief(
        realm_id,
        callback.from_user,
        tile_id,
    )
    await _ok(callback)
    await reply_guide(callback.message, engine.guide_text())
    await reply_game(
        callback.message,
        msg,
        reply_markup=fief_home_kb(engine, fief["id"]),
    )
    await post_realm_public(
        callback.bot, realm_id, format_join_announce(engine.fief_label(fief))
    )


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick_starter(callback: CallbackQuery) -> None:
    try:
        await _finish_starter_pick(callback)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_pick_starter")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("st:"))
async def cb_status(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        engine.remember_last_realm(callback.from_user.id, fief["realm_id"])
        dm_mod.clear_pending(callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_status")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("home:"))
async def cb_home(callback: CallbackQuery) -> None:
    """Вернуться на статус + домашние хабы."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        engine.remember_last_realm(callback.from_user.id, fief["realm_id"])
        dm_mod.clear_pending(callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_home")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("more:"))
async def cb_more(callback: CallbackQuery) -> None:
    """Старая кнопка \"Ещё\": обновляем сообщение до нового домашнего меню."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        engine.remember_last_realm(callback.from_user.id, fief["realm_id"])
        await callback.answer("Меню обновлено")
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_more")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("hub:"))
async def cb_hub(callback: CallbackQuery) -> None:
    """Хабы Дела (e) / Связи (v)."""
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Неизвестное меню", show_alert=True)
            return
        kind = parts[1]
        fief_id = int(parts[2])
        if kind not in {"e", "v"}:
            await callback.answer("Неизвестное меню", show_alert=True)
            return
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        open_, hint = fief_raid_pact_state(engine, fief)
        await _ok(callback)
        if kind == "e":
            await answer_html(
                callback.message,
                "Дела - действия за 1 ход:",
                reply_markup=estate_hub_kb(
                    fief_id,
                    raid_pact_open=open_,
                    lock_hint=hint,
                ),
            )
            return
        await answer_html(
            callback.message,
            "Связи - без траты действия:",
            reply_markup=valley_hub_kb(
                fief_id,
                raid_pact_open=open_,
                lock_hint=hint,
            ),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_hub")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("prep:"))
async def cb_prepared_intents(callback: CallbackQuery) -> None:
    """Исходящие заявки: набеги и обозы - просмотр и снятие."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        await _ok(callback)
        await _reply_prepared_intents(callback.message, engine, fief_id)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_prepared_intents")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("rum:"))
async def cb_rumors(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.rumors_text(fief["realm_id"]),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_rumors")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("hld:"))
async def cb_holdings(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.holdings_text(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_holdings")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("ftv:"))
async def cb_force_tick_vote_removed(callback: CallbackQuery) -> None:
    """Старые кнопки \"Тик сейчас\": обновить дом под новую кнопку etv."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        await callback.answer("Голосование снова в меню дома.", show_alert=True)
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_force_tick_vote_removed")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("etv:"))
async def cb_early_tick_vote(callback: CallbackQuery) -> None:
    """Голос / снятие голоса за досрочный тик континента."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        result = engine.toggle_early_tick_vote(fief_id, callback.from_user.id)
        await callback.answer(result.alert, show_alert=True)
        if result.locked and result.early_tick_at is not None:
            fief = engine.fief_by_id(fief_id) or {}
            realm = engine.get_realm(fief.get("realm_id")) or {}
            world = None
            if realm.get("world_id") is not None:
                world = engine.world(int(realm["world_id"]))
            if world is not None:
                text = engine.early_tick_lock_announcement(
                    result.early_tick_at, world
                )
                bot = callback.bot
                for uid in result.notify_user_ids:
                    try:
                        await send_game(bot, int(uid), text)
                    except Exception:
                        logger.warning(
                            "early tick notify failed user=%s",
                            uid,
                            exc_info=True,
                        )
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_early_tick_vote")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("lock:"))
async def cb_lock_hint(callback: CallbackQuery) -> None:
    """Пояснение по закрытому Набегу/Пакту - без трат."""
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        # lock:rad:{fid} | lock:pct:{fid}
        fief_id = int(parts[2])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        realm = engine.get_realm(fief["realm_id"])
        day_number = int(realm["day_number"]) if realm else 1
        msg = raid_pact_lock_message(
            onboard_step=int(fief.get("onboard_step") or 0),
            day_number=day_number,
        )
        await callback.answer(msg, show_alert=True)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_lock_hint")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("map:"))
async def cb_map(callback: CallbackQuery) -> None:
    """Список долин континента для просмотра карт."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        realm = engine.get_realm(fief["realm_id"])
        world_id = realm.get("world_id") if realm else None
        if world_id is None:
            await _ok(callback)
            await reply_map_photo(
                callback.message,
                engine,
                engine.map_photo(fief["realm_id"], highlight_fief_id=fief_id),
                reply_markup=map_view_kb(fief_id),
            )
            return
        realms = engine.realms_of_world(int(world_id))
        await _ok(callback)
        await reply_game(
            callback.message,
            "Карты долин континента - выберите долину:",
            reply_markup=map_realms_kb(
                fief_id, realms, home_realm_id=int(fief["realm_id"])
            ),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_map")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("mapr:"))
async def cb_map_realm(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        _, fid_s, rid_s = callback.data.split(":", 2)
        fief_id = int(fid_s)
        view_realm_id = int(rid_s)
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        home = engine.get_realm(fief["realm_id"])
        view = engine.get_realm(view_realm_id)
        if not view:
            await callback.answer("Долина не найдена", show_alert=True)
            return
        if home and home.get("world_id") is not None:
            if view.get("world_id") != home.get("world_id"):
                await callback.answer("Другой континент", show_alert=True)
                return
        highlight = fief_id if int(view_realm_id) == int(fief["realm_id"]) else None
        await _ok(callback)
        await reply_map_photo(
            callback.message,
            engine,
            engine.map_photo(view_realm_id, highlight_fief_id=highlight),
            reply_markup=map_view_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_map_realm")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("gd:"))
async def cb_guide(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_guide(
            callback.message,
            engine.guide_text(),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_guide")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("clm:"))
async def cb_claim(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)

        if len(parts) == 2:
            claimable, tile_meta, next_tile_count = dm_mod.claim_offer_data(
                engine, fief
            )
            text, kb = claim_offer(
                fief_id,
                claimable,
                next_tile_count=next_tile_count,
                tile_meta=tile_meta,
                empty_text="Нет клеток для занятия.",
                prompt_text="Выберите клетку:",
            )
            await _ok(callback)
            await answer_html(callback.message, text, reply_markup=kb)
            return

        x, y = int(parts[2]), int(parts[3])
        msg = engine.claim_tile(fief_id, x, y)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=fief_home_kb(engine, fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_claim")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("bld:"))
async def cb_build(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)

        if len(parts) == 2:
            tiles, cost_mult = engine.build_options(fief_id)
            await _ok(callback)
            await answer_html(
                callback.message,
                "Выберите здание:",
                reply_markup=dm_mod.building_types_kb(
                    fief_id, tiles, cost_mult=cost_mult
                ),
            )
            return

        building = parts[2]
        if building not in B.PLAYER_BUILDINGS:
            await callback.answer("Неизвестное здание", show_alert=True)
            return

        if len(parts) == 3:
            tiles, cost_mult = engine.build_options(fief_id)
            await _ok(callback)
            await answer_html(
                callback.message,
                f"Клетка для \"{B.BUILDING_NAMES_RU[building]}\":",
                reply_markup=dm_mod.build_tiles_kb(
                    fief_id, building, tiles, cost_mult=cost_mult
                ),
            )
            return

        x, y = int(parts[3]), int(parts[4])
        msg = engine.build_or_upgrade(fief_id, x, y, building)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=fief_home_kb(engine, fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_build")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("pat:"))
async def cb_patrol(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)

        if len(parts) == 2:
            await _ok(callback)
            await answer_html(
                callback.message,
                dm_mod.patrol_confirm_text(),
                reply_markup=dm_mod.patrol_confirm_kb(fief_id),
            )
            return

        if parts[2] != "ok":
            await callback.answer("Неизвестное действие", show_alert=True)
            return

        msg = engine.patrol(fief_id)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=fief_home_kb(engine, fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_patrol")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("pend:cancel:"))
async def cb_pending_cancel(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[2])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        dm_mod.clear_pending(callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_pending_cancel")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("rad:"))
async def cb_raid(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        open_, _hint = fief_raid_pact_state(engine, fief)
        if not open_:
            realm = engine.get_realm(fief["realm_id"])
            day_number = int(realm["day_number"]) if realm else 1
            await callback.answer(
                raid_pact_lock_message(
                    onboard_step=int(fief.get("onboard_step") or 0),
                    day_number=day_number,
                ),
                show_alert=True,
            )
            return

        if len(parts) == 2:
            others = engine.list_raid_target_fiefs(fief_id)
            text, kb = raid_targets_offer(
                fief_id,
                dm_mod.raid_target_rows(others, engine),
                empty_text="Некого грабить.",
                prompt_text=(
                    "Выберите цель (любая долина континента).\n"
                    "Точная сила скрыта - смотрите слухи или спрашивайте. "
                    "Защита цели - дружина на месте, сторожка, дозор и перехват пакта."
                ),
            )
            await _ok(callback)
            await answer_html(callback.message, text, reply_markup=kb)
            return

        victim_id = int(parts[2])
        dm_mod.set_pending(
            callback.from_user.id,
            {"kind": "raid_might", "fief_id": fief_id, "victim_id": victim_id},
        )
        await _ok(callback)
        await answer_html(
            callback.message,
            f"Сколько силы отправить ночью? (мин. {B.RAID_MIN_MIGHT})\n"
            "Дружина уйдёт сразу и не защищает дом до возвращения. "
            "Заявку можно снять до середины окна тика.\n"
            "Или напишите \"отмена\".",
            reply_markup=dm_mod.pending_cancel_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_raid")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("radtruce:"))
async def cb_raid_truce_toggle(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        pending = dm_mod.get_pending(callback.from_user.id) or {}
        if pending.get("kind") != "raid_confirm" or int(pending.get("fief_id") or 0) != fief_id:
            await callback.answer("Сначала укажите силу набега.", show_alert=True)
            return
        pending["open_truce"] = not bool(pending.get("open_truce"))
        dm_mod.set_pending(callback.from_user.id, pending)
        await _ok(callback)
        await callback.message.edit_reply_markup(
            reply_markup=dm_mod.raid_confirm_kb(
                fief_id,
                show_truce=True,
                open_truce=bool(pending.get("open_truce")),
            )
        )
    except Exception:
        logger.exception("cb_raid_truce_toggle")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("radok:"))
async def cb_raid_confirm(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        pending = dm_mod.get_pending(callback.from_user.id) or {}
        if pending.get("kind") != "raid_confirm" or int(pending.get("fief_id") or 0) != fief_id:
            await callback.answer("Сначала укажите силу набега.", show_alert=True)
            return
        might = int(pending.get("might") or 0)
        victim_id = int(pending.get("victim_id") or 0)
        open_truce = bool(pending.get("open_truce"))
        result = engine.declare_raid(
            fief_id, victim_id, might, open_truce=open_truce
        )
        dm_mod.clear_pending(callback.from_user.id)
        await _ok(callback)
        await answer_html(
            callback.message,
            result.dm_text,
            reply_markup=dm_mod.raid_cancel_intent_kb(fief_id, result.intent_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_raid_confirm")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("radx:"))
async def cb_raid_cancel_intent(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        intent_id = int(parts[2])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        msg = engine.cancel_raid_intent(fief_id, intent_id)
        await _ok(callback)
        await _reply_prepared_intents(
            callback.message, engine, fief_id, prefix=msg
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_raid_cancel_intent")
        await callback.answer("Ошибка", show_alert=True)


async def _finish_transfer_declare(
    callback: CallbackQuery,
    engine,
    *,
    fief_id: int,
    target_fief_id: int,
    res: str,
    amt: int,
) -> None:
    sender = engine.fief_by_id(fief_id)
    receiver = engine.fief_by_id(target_fief_id)
    result = engine.declare_caravan(fief_id, target_fief_id, res, amt)
    dm_mod.clear_pending(callback.from_user.id)
    await reply_game(
        callback.message,
        result.dm_text,
        reply_markup=dm_mod.caravan_cancel_intent_kb(fief_id, result.intent_id),
    )
    if receiver:
        engine.ensure_user(callback.from_user)
        try:
            await send_game(
                callback.bot,
                int(receiver["user_id"]),
                result.receiver_dm_text,
            )
        except Exception:
            logger.warning(
                "caravan DM to receiver %s failed", receiver.get("user_id")
            )
    if sender and result.is_public and result.public_declare_text:
        await post_continent_public(
            callback.bot,
            sender["realm_id"],
            result.public_declare_text,
        )


@router.callback_query(F.data.startswith("snd:"))
async def cb_send(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = engine.require_owned_fief(fief_id, callback.from_user.id)
        user_id = callback.from_user.id
        action = parts[2] if len(parts) > 2 else ""

        if action == "":
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_PICK,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                },
            )
            text, kb = transfer_entry(engine, fief)
            await _ok(callback)
            await reply_game(callback.message, text, reply_markup=kb)
            return

        if action == "find":
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_TARGET,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                },
            )
            text, kb = transfer_find_prompt(fief_id)
            await _ok(callback)
            await reply_game(callback.message, text, reply_markup=kb)
            return

        if action == "t" and len(parts) >= 4:
            target_id = int(parts[3])
            if target_id == fief_id:
                await callback.answer("Нельзя отправить себе", show_alert=True)
                return
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_RESOURCE,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                    "target_fief_id": target_id,
                },
            )
            engine.collect_for_fief(fief_id)
            fief = engine.fief_by_id(fief_id) or fief
            text, kb = transfer_resource_step(engine, fief, target_id)
            await _ok(callback)
            await reply_game(callback.message, text, reply_markup=kb)
            return

        if action == "r" and len(parts) >= 4:
            res = str(parts[3])
            pending = dm_mod.get_pending(user_id) or {}
            if (
                pending.get("kind") not in {KIND_SEND_RESOURCE, KIND_SEND_AMOUNT, KIND_SEND_CONFIRM}
                or int(pending.get("fief_id") or 0) != fief_id
            ):
                await callback.answer("Сначала выберите получателя", show_alert=True)
                return
            if res not in tradeable_keys():
                await callback.answer("Этот ресурс нельзя передать", show_alert=True)
                return
            target_id = int(pending["target_fief_id"])
            engine.collect_for_fief(fief_id)
            fief = engine.fief_by_id(fief_id) or fief
            have = stash_amount(fief, res)
            if have <= 0:
                await callback.answer("Недостаточно ресурса", show_alert=True)
                return
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_AMOUNT,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                    "target_fief_id": target_id,
                    "res": res,
                },
            )
            text, kb = transfer_amount_step(
                engine, fief, target_fief_id=target_id, res=res
            )
            await _ok(callback)
            await reply_game(callback.message, text, reply_markup=kb)
            return

        if action == "a" and len(parts) >= 4:
            pending = dm_mod.get_pending(user_id) or {}
            if (
                pending.get("kind") not in {KIND_SEND_AMOUNT, KIND_SEND_CONFIRM}
                or int(pending.get("fief_id") or 0) != fief_id
            ):
                await callback.answer("Сначала выберите ресурс", show_alert=True)
                return
            res = str(pending.get("res") or "")
            target_id = int(pending["target_fief_id"])
            if parts[3] == "x":
                engine.collect_for_fief(fief_id)
                fief = engine.fief_by_id(fief_id) or fief
                have = stash_amount(fief, res)
                dm_mod.set_pending(
                    user_id,
                    {
                        "kind": KIND_SEND_AMOUNT,
                        "fief_id": fief_id,
                        "realm_id": fief["realm_id"],
                        "target_fief_id": target_id,
                        "res": res,
                        "custom": True,
                    },
                )
                text, kb = transfer_custom_amount_step(
                    fief_id, res=res, have=have
                )
                await _ok(callback)
                await reply_game(callback.message, text, reply_markup=kb)
                return
            amt = int(parts[3])
            engine.collect_for_fief(fief_id)
            fief = engine.fief_by_id(fief_id) or fief
            have = stash_amount(fief, res)
            if amt <= 0 or amt > have:
                await callback.answer("Недостаточно ресурса", show_alert=True)
                return
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_CONFIRM,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                    "target_fief_id": target_id,
                    "res": res,
                    "amt": amt,
                },
            )
            text, kb = transfer_confirm_step(
                engine,
                fief,
                target_fief_id=target_id,
                res=res,
                amt=amt,
            )
            await _ok(callback)
            await reply_game(callback.message, text, reply_markup=kb)
            return

        if action == "ok":
            pending = dm_mod.get_pending(user_id) or {}
            if (
                pending.get("kind") != KIND_SEND_CONFIRM
                or int(pending.get("fief_id") or 0) != fief_id
            ):
                await callback.answer("Нет готовой отправки", show_alert=True)
                return
            await _finish_transfer_declare(
                callback,
                engine,
                fief_id=fief_id,
                target_fief_id=int(pending["target_fief_id"]),
                res=str(pending["res"]),
                amt=int(pending["amt"]),
            )
            await _ok(callback)
            return

        if action == "back" and len(parts) >= 4:
            pending = dm_mod.get_pending(user_id) or {}
            if int(pending.get("fief_id") or 0) != fief_id:
                await callback.answer("Сессия устарела", show_alert=True)
                return
            step = parts[3]
            if step not in {"res", "amt"}:
                await callback.answer("Неизвестная команда", show_alert=True)
                return
            await _ok(callback)
            if step == "res":
                target_id = int(pending.get("target_fief_id") or 0)
                dm_mod.set_pending(
                    user_id,
                    {
                        "kind": KIND_SEND_RESOURCE,
                        "fief_id": fief_id,
                        "realm_id": fief["realm_id"],
                        "target_fief_id": target_id,
                    },
                )
                engine.collect_for_fief(fief_id)
                fief = engine.fief_by_id(fief_id) or fief
                text, kb = transfer_resource_step(engine, fief, target_id)
                await reply_game(callback.message, text, reply_markup=kb)
                return
            target_id = int(pending.get("target_fief_id") or 0)
            res = str(pending.get("res") or "")
            dm_mod.set_pending(
                user_id,
                {
                    "kind": KIND_SEND_AMOUNT,
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                    "target_fief_id": target_id,
                    "res": res,
                },
            )
            engine.collect_for_fief(fief_id)
            fief = engine.fief_by_id(fief_id) or fief
            text, kb = transfer_amount_step(
                engine, fief, target_fief_id=target_id, res=res
            )
            await reply_game(callback.message, text, reply_markup=kb)
            return

        await callback.answer("Неизвестная команда", show_alert=True)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_send")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("cvx:"))
async def cb_caravan_cancel_intent(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        intent_id = int(parts[2])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        msg = engine.cancel_caravan_intent(fief_id, intent_id)
        await _ok(callback)
        await _reply_prepared_intents(
            callback.message, engine, fief_id, prefix=msg
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_caravan_cancel_intent")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("zsx:"))
async def cb_cover_cancel_intent(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        intent_id = int(parts[2])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        msg = engine.cancel_cover_stance_intent(fief_id, intent_id)
        await _ok(callback)
        await _reply_prepared_intents(
            callback.message, engine, fief_id, prefix=msg
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_cover_cancel_intent")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("covok:"))
async def cb_cover_confirm(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        engine.require_owned_fief(fief_id, callback.from_user.id)
        pending = dm_mod.get_pending(callback.from_user.id) or {}
        if pending.get("kind") != "cover_confirm" or int(pending.get("fief_id") or 0) != fief_id:
            await callback.answer("Сначала укажите силу заставы.", show_alert=True)
            return
        budget = int(pending.get("budget") or 0)
        mode = str(pending.get("mode") or "any")
        target_raw = pending.get("target_fief_id")
        msg = engine.set_cover_stance(
            fief_id,
            mode=mode,
            budget=budget,
            target_fief_id=int(target_raw) if target_raw is not None else None,
        )
        dm_mod.clear_pending(callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_cover_confirm")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("pct:"))
async def cb_pact(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        if parts[1] in (
            "new",
            "inv",
            "leave",
            "cov",
            "acc",
            "dec",
            "zst",
            "zsd",
            "zsa",
            "zss",
            "zstt",
        ):
            action = parts[1]
            fief_id = int(parts[2])
        else:
            action = "menu"
            fief_id = int(parts[1])

        fief = engine.require_owned_fief(fief_id, callback.from_user.id)

        if action in ("acc", "dec"):
            invite_id = int(parts[3])
            if action == "acc":
                invite = engine.get_pact_invite(invite_id)
                pact = engine.get_pact(invite["pact_id"]) if invite else None
                msg = engine.accept_pact_invite(fief_id, invite_id)
                await _ok(callback)
                await reply_game(
                    callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
                )
                if pact:
                    await post_realm_public(
                        callback.bot,
                        fief["realm_id"],
                        format_pact_join_announce(engine.fief_label(fief), pact["name"]),
                    )
            else:
                msg = engine.decline_pact_invite(fief_id, invite_id)
                await _ok(callback)
                await reply_game(
                    callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
                )
            return

        open_, _hint = fief_raid_pact_state(engine, fief)
        if not open_:
            realm = engine.get_realm(fief["realm_id"])
            day_number = int(realm["day_number"]) if realm else 1
            await callback.answer(
                raid_pact_lock_message(
                    onboard_step=int(fief.get("onboard_step") or 0),
                    day_number=day_number,
                ),
                show_alert=True,
            )
            return

        if action == "menu":
            in_pact = bool(fief.get("pact_id"))
            is_founder = False
            menu_text = "Вы не в пакте."
            if in_pact:
                pact = engine.get_pact(fief["pact_id"])
                is_founder = bool(pact and pact["founder_fief_id"] == fief_id)
                menu_text = f"Пакт \"{pact['name']}\"." if pact else "Пакт."
            text, kb = pact_menu_offer(
                fief_id,
                in_pact=in_pact,
                is_founder=is_founder,
                text=menu_text,
            )
            await _ok(callback)
            await answer_html(callback.message, text, reply_markup=kb)
            return

        if action == "new":
            dm_mod.set_pending(
                callback.from_user.id,
                {"kind": "pact_name", "fief_id": fief_id},
            )
            await _ok(callback)
            await answer_html(
                callback.message,
                "Введите название пакта:\nИли напишите \"отмена\".",
                reply_markup=dm_mod.pending_cancel_kb(fief_id),
            )
            return

        if action == "inv":
            dm_mod.set_pending(
                callback.from_user.id,
                {
                    "kind": "pact_invite",
                    "fief_id": fief_id,
                    "realm_id": fief["realm_id"],
                },
            )
            await _ok(callback)
            await answer_html(
                callback.message,
                "Укажите id усадьбы или точное имя для приглашения:\n"
                "Или напишите \"отмена\".",
                reply_markup=dm_mod.pending_cancel_kb(fief_id),
            )
            return

        if action == "leave":
            pact = engine.get_pact(fief["pact_id"]) if fief.get("pact_id") else None
            pact_name = pact["name"] if pact else "?"
            msg = engine.leave_pact(fief_id)
            await _ok(callback)
            await reply_game(
                callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
            )
            await post_realm_public(
                callback.bot,
                fief["realm_id"],
                format_pact_leave_announce(
                    engine.fief_label(fief),
                    pact_name,
                    dissolved="распущен" in msg,
                ),
            )
            return

        if action == "zst":
            await _ok(callback)
            await answer_html(
                callback.message,
                (
                    "Застава на эту ночь: выберите стойку. "
                    f"Сила от {B.COVER_BUDGET_MIN} (потолка нет, лишь сколько есть) "
                    "уйдёт в резерв до середины окна тика."
                ),
                reply_markup=cover_stance_kb(fief_id),
            )
            return

        if action == "zsd":
            msg = engine.set_cover_stand_down(fief_id)
            await _ok(callback)
            await reply_game(
                callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
            )
            return

        if action == "zsa":
            dm_mod.set_pending(
                callback.from_user.id,
                {
                    "kind": "cover_budget",
                    "fief_id": fief_id,
                    "mode": "any",
                },
            )
            await _ok(callback)
            await answer_html(
                callback.message,
                (
                    f"Сколько силы на заставу любого союзника? "
                    f"От {B.COVER_BUDGET_MIN}, потолка нет.\n"
                    "Или напишите \"отмена\"."
                ),
                reply_markup=dm_mod.pending_cancel_kb(fief_id),
            )
            return

        if action == "zss":
            if not fief.get("pact_id"):
                raise ValueError("Нужен пакт")
            allies: list[tuple[int, str]] = []
            for m in engine.db.pact_members(int(fief["pact_id"])):
                if int(m["id"]) == int(fief_id):
                    continue
                allies.append((int(m["id"]), engine.fief_label(m)))
            if not allies:
                raise ValueError("В пакте пока нет других союзников")
            await _ok(callback)
            await answer_html(
                callback.message,
                "Кого прикрыть этой ночью?",
                reply_markup=cover_ally_pick_kb(fief_id, allies),
            )
            return

        if action == "zstt":
            target_id = int(parts[3])
            dm_mod.set_pending(
                callback.from_user.id,
                {
                    "kind": "cover_budget",
                    "fief_id": fief_id,
                    "mode": "specific",
                    "target_fief_id": target_id,
                },
            )
            tgt = engine.fief_by_id(target_id)
            tname = engine.fief_label(tgt) if tgt else str(target_id)
            await _ok(callback)
            await answer_html(
                callback.message,
                (
                    f"Сколько силы на заставу у {tname}? "
                    f"От {B.COVER_BUDGET_MIN}, потолка нет.\n"
                    "Или напишите \"отмена\"."
                ),
                reply_markup=dm_mod.pending_cancel_kb(fief_id),
            )
            return

        if action == "cov":
            # Старые кнопки вкл/выкл → стойка / в стороне
            enabled = parts[3] == "1"
            msg = engine.set_cover(fief_id, enabled)
            await _ok(callback)
            await reply_game(
                callback.message, msg, reply_markup=fief_home_kb(engine, fief_id)
            )
            return
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_pact")
        await callback.answer("Ошибка", show_alert=True)
