"""Доставка патч-вестников в лички владельцев усадеб долин."""
from __future__ import annotations

import logging

from aiogram import Bot

from app.domain.patch_notes import format_patch_announcement, pending_patch_notes
from app.notifier import FanoutResult, post_realm_public
from app.wiring import get_engine

logger = logging.getLogger(__name__)


def _realm_counts_as_delivered(result: FanoutResult) -> bool:
    """Хотя бы одна личка долины приняла вестник (best-effort, без ретрая-спама)."""
    return result.sent > 0


def should_mark_patch_announced(
    *,
    realm_count: int,
    populated: int,
    ok_count: int,
    hard_fails: int,
) -> bool:
    """True - писать mark; False - отложить доставку на следующий опрос."""
    if populated > 0 and ok_count == 0:
        return False
    if populated == 0 and hard_fails > 0 and realm_count > 0:
        return False
    return True


async def announce_pending_patches(bot: Bot) -> list[str]:
    """Публикует ещё не объявленные патчи во все долины.

    Сначала рассылка, потом mark в БД - только если хотя бы одна населённая
    долина приняла сообщение (или долин нет / все пустые без жёстких сбоев).
    """
    engine = get_engine()
    announced = engine.announced_patch_names()
    pending = pending_patch_notes(announced)
    if not pending:
        return []

    realms = engine.realms_to_announce()
    delivered: list[str] = []
    for note in pending:
        text = format_patch_announcement(note)
        ok_count = 0
        populated = 0
        hard_fails = 0
        for realm in realms:
            rid = int(realm["id"])
            result = await post_realm_public(bot, rid, text)
            if result.targets > 0:
                populated += 1
                if _realm_counts_as_delivered(result):
                    ok_count += 1
            elif result.ok:
                pass
            else:
                hard_fails += 1
        if not should_mark_patch_announced(
            realm_count=len(realms),
            populated=populated,
            ok_count=ok_count,
            hard_fails=hard_fails,
        ):
            logger.warning(
                "patch announce deferred id=%s: populated=%s ok=%s hard_fails=%s",
                note.id,
                populated,
                ok_count,
                hard_fails,
            )
            continue
        engine.mark_patch_announced(note.id)
        delivered.append(note.id)
        logger.info(
            "patch announced id=%s ok_realms=%s/%s populated=%s",
            note.id,
            ok_count,
            len(realms),
            populated,
        )
    return delivered
