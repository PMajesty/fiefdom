"""CallbackQuery: меню усадьбы, клейм, стройка, набег, рынок, пакт, старт."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery

from app import balance as B
from app.domain.economy import adjacent_claimable
from app.handlers import dm as dm_mod
from app.handlers.shared import get_engine, main_menu_kb, reply_game
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


_dm = Router(name="callbacks_dm")
_dm.callback_query.filter(F.chat.type == ChatType.PRIVATE)
router.include_router(_dm)


def _ensure_owner(engine, fief_id: int, user_id: int) -> dict:
    fief = engine.db.get_fief(fief_id)
    if not fief or fief["user_id"] != user_id:
        raise ValueError("Это не ваша усадьба")
    return fief


@_dm.callback_query(F.data.startswith("pick:"))
async def cb_pick_starter(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        _, realm_s, tile_s = callback.data.split(":", 2)
        realm_id = int(realm_s)
        tile_id = int(tile_s)
        fief, msg = engine.join_fief(realm_id, callback.from_user, tile_id)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief["id"]))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_pick_starter")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("st:"))
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
            reply_markup=main_menu_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_status")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("map:"))
async def cb_map(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)
        await _ok(callback)
        await reply_game(
            callback.message,
            engine.map_text(fief["realm_id"], highlight_fief_id=fief_id),
            reply_markup=main_menu_kb(fief_id),
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_map")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("mkt:"))
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


@_dm.callback_query(F.data.startswith("clm:"))
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
            claimable = sorted(
                adjacent_claimable(
                    owned, {(t.x, t.y): t for t in views}, for_fief_id=fief_id
                )
            )
            await _ok(callback)
            if not claimable:
                await answer_html(callback.message, "Нет клеток для клейма.")
                return
            await answer_html(
                callback.message,
                "Выберите клетку:",
                reply_markup=dm_mod.claimable_kb(fief_id, claimable),
            )
            return

        x, y = int(parts[2]), int(parts[3])
        msg = engine.claim_tile(fief_id, x, y)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_claim")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("bld:"))
async def cb_build(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if len(parts) == 2:
            await _ok(callback)
            await answer_html(
                callback.message,
                "Выберите здание:",
                reply_markup=dm_mod.building_types_kb(fief_id),
            )
            return

        building = parts[2]
        if building not in B.BUILDING_COSTS:
            await callback.answer("Неизвестное здание", show_alert=True)
            return

        if len(parts) == 3:
            tiles = [
                t
                for t in engine.db.fief_tiles(fief_id)
                if not t.get("is_overgrown")
            ]
            await _ok(callback)
            await answer_html(
                callback.message,
                f"Клетка для «{B.BUILDING_NAMES_RU[building]}»:",
                reply_markup=dm_mod.build_tiles_kb(fief_id, building, tiles),
            )
            return

        x, y = int(parts[3]), int(parts[4])
        msg = engine.build_or_upgrade(fief_id, x, y, building)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_build")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("pat:"))
async def cb_patrol(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        fief_id = int(callback.data.split(":")[1])
        _ensure_owner(engine, fief_id, callback.from_user.id)
        msg = engine.patrol(fief_id)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_patrol")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("rad:"))
async def cb_raid(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        fief_id = int(parts[1])
        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if len(parts) == 2:
            others = [
                o
                for o in engine.db.list_fiefs(fief["realm_id"])
                if o["id"] != fief_id and not o.get("frozen")
            ]
            await _ok(callback)
            if not others:
                await answer_html(callback.message, "Некого грабить.")
                return
            await answer_html(
                callback.message,
                "Выберите цель:",
                reply_markup=dm_mod.raid_targets_kb(fief_id, others),
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
            f"Сколько силы отправить? (мин. {B.RAID_MIN_MIGHT})",
        )
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_raid")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("trd:"))
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
                "(отдаю количество → хочу количество).",
            )
            return

        trade_id = int(parts[3])
        if action == "a":
            msg = engine.accept_trade(fief_id, trade_id)
        else:
            msg = engine.cancel_trade(fief_id, trade_id)
        await _ok(callback)
        await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_trade")
        await callback.answer("Ошибка", show_alert=True)


@_dm.callback_query(F.data.startswith("pct:"))
async def cb_pact(callback: CallbackQuery) -> None:
    engine = get_engine()
    try:
        parts = callback.data.split(":")
        if parts[1] in ("new", "inv", "leave", "cov"):
            action = parts[1]
            fief_id = int(parts[2])
        else:
            action = "menu"
            fief_id = int(parts[1])

        fief = _ensure_owner(engine, fief_id, callback.from_user.id)

        if action == "menu":
            in_pact = bool(fief.get("pact_id"))
            is_founder = False
            text = "Вы не в пакте."
            if in_pact:
                pact = engine.db.get_pact(fief["pact_id"])
                is_founder = bool(pact and pact["founder_fief_id"] == fief_id)
                text = f"Пакт «{pact['name']}»." if pact else "Пакт."
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
            await answer_html(callback.message, "Введите название пакта:")
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
                "Укажите id усадьбы или точное имя для приглашения:",
            )
            return

        if action == "leave":
            msg = engine.leave_pact(fief_id)
            await _ok(callback)
            await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
            return

        if action == "cov":
            enabled = parts[3] == "1"
            msg = engine.set_cover(fief_id, enabled)
            await _ok(callback)
            await reply_game(callback.message, msg, reply_markup=main_menu_kb(fief_id))
            return
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
    except Exception:
        logger.exception("cb_pact")
        await callback.answer("Ошибка", show_alert=True)
