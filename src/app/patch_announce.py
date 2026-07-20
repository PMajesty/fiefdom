"""Доставка патч-вестников в групповые чаты долин."""
from __future__ import annotations

import logging

from aiogram import Bot

from app.domain.patch_notes import format_patch_announcement, pending_patch_notes
from app.wiring import get_engine
from app.handlers.shared import post_realm_public

logger = logging.getLogger(__name__)


async def announce_pending_patches(bot: Bot) -> list[str]:
    """Публикует ещё не объявленные патчи во все долины.

    Сначала рассылка, потом mark в БД - только если хотя бы одна долина
    приняла сообщение (или долин нет). Иначе повтор на следующем опросе.
    """
    engine = get_engine()
    announced = engine.db.list_announced_patch_names()
    pending = pending_patch_notes(announced)
    if not pending:
        return []

    realms = engine.db.list_realms()
    delivered: list[str] = []
    for note in pending:
        text = format_patch_announcement(note)
        ok_count = 0
        for realm in realms:
            rid = int(realm["id"])
            if await post_realm_public(bot, rid, text):
                ok_count += 1
        if ok_count == 0 and realms:
            logger.warning(
                "patch announce deferred id=%s: no realm accepted the message",
                note.id,
            )
            continue
        engine.db.mark_patch_announced(note.id)
        delivered.append(note.id)
        logger.info(
            "patch announced id=%s ok_realms=%s/%s",
            note.id,
            ok_count,
            len(realms),
        )
    return delivered
