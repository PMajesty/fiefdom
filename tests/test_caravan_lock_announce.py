"""Midday-confirm уведомления обозов и доставка continent notices."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("ADMIN_USER_ID", "42")

import pytest

from app.domain.caravans import (
    format_caravan_cargo_parts,
    group_caravan_routes,
)
from app.domain.raids import RaidNightPartyNotice
from app import balance as B


def test_group_caravan_routes_sums_same_pair():
    intents = [
        {
            "id": 1,
            "fief_id": 10,
            "payload": {
                "receiver_id": 20,
                "res": B.RES_GRAIN,
                "amt": 12,
                "sender_realm_id": 1,
                "receiver_realm_id": 2,
            },
        },
        {
            "id": 2,
            "fief_id": 10,
            "payload": {
                "receiver_id": 20,
                "res": B.RES_GRAIN,
                "amt": 8,
                "sender_realm_id": 1,
                "receiver_realm_id": 2,
            },
        },
        {
            "id": 3,
            "fief_id": 10,
            "payload": {
                "receiver_id": 30,
                "res": B.RES_GOODS,
                "amt": 5,
                "sender_realm_id": 1,
                "receiver_realm_id": 3,
            },
        },
    ]
    routes = group_caravan_routes(intents)
    assert len(routes) == 2
    assert routes[0].sender_fief_id == 10
    assert routes[0].receiver_fief_id == 20
    assert routes[0].total_amt == 20
    assert routes[0].amounts[B.RES_GRAIN] == 20
    assert routes[0].intent_ids == (1, 2)
    assert routes[1].receiver_fief_id == 30
    assert routes[1].total_amt == 5


def test_format_caravan_cargo_parts_joins_resources():
    text = format_caravan_cargo_parts({B.RES_GRAIN: 12, B.RES_GOODS: 8})
    assert "12" in text and "8" in text
    assert " и " in text


@pytest.mark.asyncio
async def test_deliver_raid_notices_posts_continent():
    from app import scheduler as sched

    bot = MagicMock()
    bot.send_message = AsyncMock()
    notices = [
        RaidNightPartyNotice(
            user_id=None,
            realm_id=10,
            text="📦 Обоз: итог",
            kind="continent",
        ),
        RaidNightPartyNotice(
            user_id=None,
            realm_id=10,
            text="📦 Обоз: итог",
            kind="continent",
        ),
    ]
    with patch.object(
        sched, "post_continent_public", new_callable=AsyncMock
    ) as post:
        await sched._deliver_raid_notices(bot, notices)
    post.assert_awaited_once_with(bot, 10, "📦 Обоз: итог")


def test_transfer_confirm_summary_mentions_midday_reveal():
    from app.ui.keyboards.transfer import transfer_confirm_summary

    text = transfer_confirm_summary(
        receiver_label="Бета",
        res=B.RES_GRAIN,
        amt=5,
        lock_text="12:00",
        resolve_text="18:00",
    )
    assert "только вы" in text
    assert "складываются" in text
    assert str(B.CARAVAN_PUBLIC_AMOUNT) in text


def test_caravan_lock_notified_backfill_sql_marks_legacy():
    from app.database import Database

    db = MagicMock(spec=Database)
    db.cursor = MagicMock()
    db.cursor.fetchone.side_effect = [None]
    Database._apply_patch_caravan_lock_notified_backfill(db)
    sqls = [" ".join(str(c.args[0]).split()) for c in db.cursor.execute.call_args_list]
    assert any("lock_notified" in s and "caravan" in s for s in sqls)
    assert any("applied_patches" in s and "INSERT" in s.upper() for s in sqls)


@pytest.mark.asyncio
async def test_midplay_commits_when_intent_ids_without_notices():
    """Private route with no DM still commits so polls do not spin."""
    from app.domain.caravans import LockCaravanReport
    from app import scheduler as sched

    engine = MagicMock()
    engine.announce_locked_caravans.return_value = LockCaravanReport(
        announced_intent_count=1,
        notices=[],
        intent_ids=(3,),
        public_ids=(),
    )
    engine.commit_locked_caravan_announcements = MagicMock(return_value=1)
    await sched._finalize_caravan_lock_announce(MagicMock(), engine, 1)
    engine.commit_locked_caravan_announcements.assert_called_once_with(
        (3,), public_ids=()
    )


@pytest.mark.asyncio
async def test_midplay_commits_even_when_delivery_fails_to_avoid_spam():
    from app.domain.caravans import LockCaravanReport
    from app.domain.raids import RaidNightPartyNotice
    from app import scheduler as sched

    engine = MagicMock()
    engine.announce_locked_caravans.return_value = LockCaravanReport(
        announced_intent_count=1,
        notices=[
            RaidNightPartyNotice(
                user_id=200, realm_id=None, text="обоз", kind="dm"
            )
        ],
        intent_ids=(3,),
        public_ids=(),
    )
    engine.commit_locked_caravan_announcements = MagicMock(return_value=1)

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=RuntimeError("tg down"))
    await sched._finalize_caravan_lock_announce(bot, engine, 1)
    engine.commit_locked_caravan_announcements.assert_called_once_with(
        (3,), public_ids=()
    )
