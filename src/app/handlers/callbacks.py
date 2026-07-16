"""CallbackQuery: меню усадьбы, клейм, стройка, набег, рынок, пакт, старт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app import balance as B
from app.domain.economy import adjacent_claimable
from app.domain.events import minor_effect
from app.domain.guide import join_welcome_text
from app.handlers import dm as dm_mod
from app.handlers.shared import (
    announce_realm,
    fief_home_kb,
    fief_raid_pact_state,
    format_join_announce,
    format_pact_join_announce,
    format_pact_leave_announce,
    format_trade_accept_announce,
    get_engine,
    more_menu_kb,
    post_digest,
    realm_upgrade_cost_mult,
    reply_game,
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


@router.callback_query(F.data.startswith("cat:"))
async def cb_catastrophe_contribute(callback: CallbackQuery) -> None:
    """Вклад в катастрофу (группа или личка)."""
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        event_id = int(parts[1])
        action = parts[2] if len(parts) > 2 else "might5"
        ev = engine.db._fetchone("SELECT * FROM realm_events WHERE id=%s;", (event_id,))
        if not ev or ev.get("status") != "active":
            await callback.answer("Событие уже завершено", show_alert=True)
            return
        fief = engine.db.get_fief_by_user(ev["realm_id"], callback.from_user.id)
        if not fief:
            await callback.answer("Сначала получите усадьбу в личке", show_alert=True)
            return
        if action == "might5":
            amount = 5
            if fief["might"] < amount:
                await callback.answer("Недостаточно силы", show_alert=True)
                return
            first = engine.db.add_event_action(event_id, fief["id"], "might", amount)
            if not first:
                with engine.db.lock:
                    engine.db.cursor.execute(
                        """
                        UPDATE event_actions SET amount = amount + %s
                        WHERE event_id=%s AND fief_id=%s AND action_key='might';
                        """,
                        (amount, event_id, fief["id"]),
                    )
                    engine.db.commit()
            engine.db.update_fief(fief["id"], might=fief["might"] - amount)
            total = sum(int(a.get("amount") or 0) for a in engine.db.event_actions(event_id))
            await callback.answer(f"Вложено! Всего силы в котле: {total}", show_alert=True)
        else:
            await callback.answer("Неизвестное действие", show_alert=True)
    except Exception:
        logger.exception("cb_catastrophe_contribute")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("des:"))
async def cb_deserter_claim(callback: CallbackQuery) -> None:
    """Гонка за дезертира в групповом чате: первый клейм побеждает."""
    engine = get_engine()
    try:
        event_id = int(callback.data.split(":")[1])
        result = engine.claim_deserter(event_id, callback.from_user.id)
        if result == "already_taken":
            await callback.answer("Уже ушёл к другому", show_alert=True)
            return
        bonus = int(minor_effect("deserter").get("first_claim_might") or 10)
        await callback.answer(f"Дезертир в дружине! +{bonus} силы", show_alert=True)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_deserter_claim")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("drt:"))
async def cb_drought_mitigate(callback: CallbackQuery) -> None:
    """Полив засухи: товары за иммунитет этой усадьбы."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner(engine, fief_id, callback.from_user.id)
        result = engine.mitigate_drought(fief_id)
        if result == "already":
            await callback.answer("Ваши поля уже политы", show_alert=True)
            return
        await _ok(callback)
        await reply_game(
            callback.message,
            "Полив сделан - засуха больше не душит ваши фермы.\n"
            + engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_drought_mitigate")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("cpl:"))
async def cb_cattle_plague_mitigate(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner(engine, fief_id, callback.from_user.id)
        result = engine.mitigate_cattle_plague(fief_id)
        if result == "already":
            await callback.answer("Мор у вас уже снят", show_alert=True)
            return
        await _ok(callback)
        await reply_game(
            callback.message,
            "Скот забит - мор больше не душит ваши фермы.\n"
            + engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_cattle_plague_mitigate")
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
                    f"• зерно +{B.GATHER_GRAIN}\n"
                    f"• товары +{B.GATHER_GOODS}\n"
                    f"• сила +{B.GATHER_MIGHT}"
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


@router.callback_query(F.data.startswith("pick:"))
async def cb_pick_starter(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        _, realm_s, tile_s = callback.data.split(":", 2)
        realm_id = int(realm_s)
        tile_id = int(tile_s)
        fief, msg = engine.join_fief(realm_id, callback.from_user, tile_id)
        await _ok(callback)
        await reply_game(
            callback.message,
            join_welcome_text(msg),
            reply_markup=fief_home_kb(engine, fief["id"]),
        )
        await announce_realm(
            callback.bot, realm_id, format_join_announce(engine.fief_label(fief))
        )
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
    """Свернуть \"Ещё\" → статус + домашний CTA."""
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
    """Раскрыть полный набор действий."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        open_, hint = fief_raid_pact_state(engine, fief)
        progress = engine.force_tick_progress(int(fief["realm_id"]))
        force_prog = None
        if progress["available"]:
            force_prog = (progress["votes"], progress["needed"])
        await _ok(callback)
        await answer_html(
            callback.message,
            "Все действия:",
            reply_markup=more_menu_kb(
                fief_id,
                drought_mitigate=engine.fief_can_mitigate_drought(fief_id),
                cattle_plague_mitigate=engine.fief_can_mitigate_cattle_plague(fief_id),
                raid_pact_open=open_,
                lock_hint=hint,
                force_tick_progress=force_prog,
            ),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_more")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("ftv:"))
async def cb_force_tick_vote(callback: CallbackQuery) -> None:
    """Голос за досрочный тик континента (без спама в чат)."""
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner_active(engine, fief_id, callback.from_user.id)
        result = engine.cast_force_tick_vote(fief_id)
        status = result["status"]
        progress = result["progress"]

        if status == "too_few":
            await callback.answer(
                f"Нужно минимум {B.FORCE_TICK_MIN_PLAYERS} игрока на континенте",
                show_alert=True,
            )
            return
        if status == "already":
            await callback.answer(
                f"Вы уже голосуете ({progress['votes']}/{progress['needed']})",
                show_alert=True,
            )
            return
        if status == "voted":
            await callback.answer(
                f"Голос учтён: {progress['votes']}/{progress['needed']}",
                show_alert=True,
            )
            await reply_game(
                callback.message,
                engine.status_card(fief_id),
                reply_markup=fief_home_kb(engine, fief_id),
            )
            return

        tick = result.get("tick") or {}
        await callback.answer(
            "Досрочный тик континента! Сводки в чатах долин.",
            show_alert=True,
        )
        from app.scheduler import post_deserter_race

        for item in tick.get("realms") or []:
            digest = item.get("digest")
            chat_id = item.get("chat_id")
            realm_id = item.get("realm_id")
            if digest and chat_id and realm_id:
                await post_digest(callback.bot, chat_id, int(realm_id), digest)
            deserter_event = item.get("deserter_event")
            if deserter_event and chat_id:
                await post_deserter_race(callback.bot, chat_id, deserter_event)
        await reply_game(
            callback.message,
            engine.status_card(fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_force_tick_vote")
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
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.map_text(fief["realm_id"], highlight_fief_id=fief_id),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_map")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("gd:"))
async def cb_guide(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner(engine, fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.guide_text(),
            reply_markup=fief_home_kb(engine, fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_guide")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("mkt:"))
async def cb_market(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        offers = engine.db.list_open_trades(fief["realm_id"], fief_id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.market_text(fief["realm_id"], fief_id),
            reply_markup=dm_mod.market_kb(fief_id, offers),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_market")
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
            cost_mult = realm_upgrade_cost_mult(realm)
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
            cost_mult = realm_upgrade_cost_mult(realm)
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
                "Выберите цель (долина и соседи по порталу):",
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
            f"Сколько силы отправить? (мин. {B.RAID_MIN_MIGHT})\n"
            "Или напишите \"отмена\".",
            reply_markup=dm_mod.pending_cancel_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_raid")
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
            "Кому передать зерно или товары?\n"
            "Напишите id усадьбы, имя или @username.\n"
            "Силу передать нельзя. Или напишите \"отмена\".",
            reply_markup=dm_mod.pending_cancel_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_send")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("trd:"))
async def cb_trade(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        if parts[1] in ("new", "a", "c"):
            action = parts[1]
            fief_id = int(parts[2])
        else:
            action = "list"
            fief_id = int(parts[1])

        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if action == "list":
            offers = engine.db.list_open_trades(fief["realm_id"], fief_id)
            await _ok(callback)
            await reply_game(
                callback.message,
                engine.market_text(fief["realm_id"], fief_id),
                reply_markup=dm_mod.market_kb(fief_id, offers),
            )
            return

        if action == "new":
            dm_mod.set_pending(
                callback.from_user.id,
                {"kind": "trade_create", "fief_id": fief_id},
            )
            await _ok(callback)
            await reply_game(
                callback.message,
                "Отправьте лот: <code>зерно 10 товары 5</code>\n"
                "(сначала что отдаёте, потом что хотите взамен).\n"
                "Или напишите \"отмена\".",
                reply_markup=dm_mod.pending_cancel_kb(fief_id),
            )
            return

        trade_id = int(parts[3])
        seller = None
        trade = None
        if action == "a":
            trade = engine.db.get_trade(trade_id)
            if trade:
                seller = engine.db.get_fief(trade["offerer_fief_id"])
            msg = engine.accept_trade(fief_id, trade_id)
        else:
            msg = engine.cancel_trade(fief_id, trade_id)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=fief_home_kb(engine, fief_id))
        if action == "a" and seller and trade and msg.startswith("Сделка"):
            engine.ensure_user(callback.from_user)
            await announce_realm(
                callback.bot,
                fief["realm_id"],
                format_trade_accept_announce(
                    engine.fief_label(fief),
                    engine.fief_label(seller),
                    trade["give_amt"],
                    trade["give_res"],
                    trade["want_amt"],
                    trade["want_res"],
                ),
            )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_trade")
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
                    await announce_realm(
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
            await announce_realm(
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
