"""CallbackQuery: меню усадьбы, клейм, стройка, набег, караван, пакт, старт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import balance as B
from app.domain.economy import adjacent_claimable
from app.domain.resources import resource_defs
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
    post_realm_public,
    prepared_intents_kb,
    realm_upgrade_cost_mult,
    reply_game,
    reply_guide,
    reply_map_photo,
    valley_hub_kb,
)
from app.engine import raid_pact_lock_message
from app.messaging import answer_html

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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
        if len(parts) == 2:
            tiles = [
                t
                for t in engine.db.fief_tiles(fief_id)
                if not t.get("is_overgrown")
            ]
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


def _ensure_owner(engine, fief_id: int, user_id: int) -> dict:
    fief = engine.db.get_fief(fief_id)
    if not fief or fief["user_id"] != user_id:
        raise ValueError("Это не ваша усадьба")
    return fief


def _ensure_owner_active(engine, fief_id: int, user_id: int) -> dict:
    fief = _ensure_owner(engine, fief_id, user_id)
    if not engine.fief_is_active_play(fief):
        raise ValueError(
            "Сначала выберите эту долину активной "
            "(откройте усадьбу здесь или список в /start)"
        )
    return fief


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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        engine.db.set_last_realm(callback.from_user.id, fief["realm_id"])
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        engine.db.set_last_realm(callback.from_user.id, fief["realm_id"])
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        engine.db.set_last_realm(callback.from_user.id, fief["realm_id"])
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
    """Хабы Усадьба (e) / Долина (v)."""
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        open_, hint = fief_raid_pact_state(engine, fief)
        await _ok(callback)
        if kind == "e":
            await answer_html(
                callback.message,
                "Усадьба - дела за действие:",
                reply_markup=estate_hub_kb(
                    fief_id,
                    raid_pact_open=open_,
                    lock_hint=hint,
                ),
            )
            return
        await answer_html(
            callback.message,
            "Долина - связи без действия:",
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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
    """Старые кнопки \"Тик сейчас\": сообщить об отмене и обновить дом."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner(engine, fief_id, callback.from_user.id)
        await callback.answer("Досрочный тик отменён. Ждите плановый ход.", show_alert=True)
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


@router.callback_query(F.data.startswith("lock:"))
async def cb_lock_hint(callback: CallbackQuery) -> None:
    """Пояснение по закрытому Набегу/Пакту - без трат."""
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        # lock:rad:{fid} | lock:pct:{fid}
        fief_id = int(parts[2])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        realm = engine.db.get_realm(fief["realm_id"])
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        realm = engine.db.get_realm(fief["realm_id"])
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
        realms = engine.db.list_realms_by_chain(int(world_id))
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        home = engine.db.get_realm(fief["realm_id"])
        view = engine.db.get_realm(view_realm_id)
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if len(parts) == 2:
            views = engine.tile_views(fief["realm_id"])
            owned = {
                (t.x, t.y)
                for t in views
                if t.owner_fief_id == fief_id and not t.is_overgrown
            }
            by_xy = {(t.x, t.y): t for t in views}
            realm = engine.db.get_realm(fief["realm_id"])
            claimable = sorted(
                adjacent_claimable(
                    owned,
                    by_xy,
                    width=realm["width"],
                    height=realm["height"],
                    for_fief_id=fief_id,
                )
            )
            await _ok(callback)
            if not claimable:
                await answer_html(callback.message, "Нет клеток для занятия.")
                return
            tile_meta = {
                (x, y): (by_xy[(x, y)].tile_type, by_xy[(x, y)].is_overgrown)
                for x, y in claimable
                if (x, y) in by_xy
            }
            await answer_html(
                callback.message,
                "Выберите клетку:",
                reply_markup=dm_mod.claimable_kb(
                    fief_id,
                    claimable,
                    next_tile_count=len(owned) + 1,
                    tile_meta=tile_meta,
                ),
            )
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if len(parts) == 2:
            tiles = [
                t
                for t in engine.db.fief_tiles(fief_id)
                if not t.get("is_overgrown")
            ]
            realm = engine.db.get_realm(fief["realm_id"])
            cost_mult = realm_upgrade_cost_mult(engine, realm)
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
            tiles = [
                t
                for t in engine.db.fief_tiles(fief_id)
                if not t.get("is_overgrown")
            ]
            realm = engine.db.get_realm(fief["realm_id"])
            cost_mult = realm_upgrade_cost_mult(engine, realm)
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
        _ensure_owner(engine, fief_id, callback.from_user.id)

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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        open_, _hint = fief_raid_pact_state(engine, fief)
        if not open_:
            realm = engine.db.get_realm(fief["realm_id"])
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
            await _ok(callback)
            if not others:
                await answer_html(callback.message, "Некого грабить.")
                return
            await answer_html(
                callback.message,
                "Выберите цель (любая долина континента).\n"
                "Точная сила скрыта - смотрите слухи или спрашивайте. "
                "Защита цели - дружина на месте, сторожка, дозор и перехват пакта.",
                reply_markup=dm_mod.raid_targets_kb(fief_id, others, engine),
            )
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
        pending = dm_mod.pending_actions.get(callback.from_user.id) or {}
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
        pending = dm_mod.pending_actions.get(callback.from_user.id) or {}
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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


@router.callback_query(F.data.startswith("snd:"))
async def cb_send(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        dm_mod.set_pending(
            callback.from_user.id,
            {
                "kind": "send_target",
                "fief_id": fief_id,
                "realm_id": fief["realm_id"],
            },
        )
        await _ok(callback)
        await reply_game(
            callback.message,
            "Куда отправить обоз с зерном или товарами?\n"
            "Напишите id усадьбы, имя или @username.\n"
            "Обоз идёт до следующего колокола тика; пока в пути - можно вернуть. "
            f"От {B.CARAVAN_PUBLIC_AMOUNT} и больше долина увидит выезд; "
            "мелкое - только адресату. Силу везти нельзя.\n"
            "Или напишите \"отмена\".",
            reply_markup=dm_mod.pending_cancel_kb(fief_id),
        )
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
        _ensure_owner(engine, fief_id, callback.from_user.id)
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


@router.callback_query(F.data.startswith("pct:"))
async def cb_pact(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        if parts[1] in ("new", "inv", "leave", "cov", "acc", "dec"):
            action = parts[1]
            fief_id = int(parts[2])
        else:
            action = "menu"
            fief_id = int(parts[1])

        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if action in ("acc", "dec"):
            invite_id = int(parts[3])
            if action == "acc":
                invite = engine.db.get_pact_invite(invite_id)
                pact = engine.db.get_pact(invite["pact_id"]) if invite else None
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
            realm = engine.db.get_realm(fief["realm_id"])
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
            text = "Вы не в пакте."
            if in_pact:
                pact = engine.db.get_pact(fief["pact_id"])
                is_founder = bool(pact and pact["founder_fief_id"] == fief_id)
                text = f"Пакт \"{pact['name']}\"." if pact else "Пакт."
            await _ok(callback)
            await answer_html(
                callback.message,
                text,
                reply_markup=dm_mod.pact_kb(fief_id, in_pact, is_founder),
            )
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
            pact = engine.db.get_pact(fief["pact_id"]) if fief.get("pact_id") else None
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

        if action == "cov":
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
