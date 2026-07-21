"""Патч-вестники: формат, реестр и разовая доставка в долины."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import balance as B
from app.domain import patch_notes as notes_mod
from app.domain.patch_notes import (
    PATCH_NOTES,
    PatchNote,
    format_patch_announcement,
    pending_patch_notes,
)
from app.notifier import FanoutResult
from app.patch_announce import announce_pending_patches, should_mark_patch_announced


def test_format_patch_announcement_rp_shape():
    note = PatchNote(
        id="test_note",
        title="Заголовок вестника",
        body_lines=("Первая строка.", "Вторая строка."),
    )
    text = format_patch_announcement(note)
    assert "📯" in text
    assert "Вестник долины" in text
    assert "<b>Заголовок вестника</b>" in text
    assert "• Первая строка." in text
    assert "• Вторая строка." in text
    assert "\u2014" not in text
    assert "\u00ab" not in text
    assert "\u00bb" not in text


def test_pending_patch_notes_skips_announced():
    ids = {PATCH_NOTES[0].id}
    pending = pending_patch_notes(ids)
    assert all(note.id not in ids for note in pending)
    assert pending_patch_notes(set()) == list(PATCH_NOTES)


def test_shield_nerf_note_matches_balance():
    note = next(n for n in PATCH_NOTES if n.id == "raid_victim_shield_one_tick_v1")
    text = format_patch_announcement(note)
    assert str(B.RAID_VICTIM_SHIELD_TICKS) in text
    assert "щит" in text.lower()
    assert "общ" in text.lower()
    assert note.id == "raid_victim_shield_one_tick_v1"


def test_patch_note_ids_unique():
    ids = [note.id for note in PATCH_NOTES]
    assert len(ids) == len(set(ids))
    assert all(note.id and note.title and note.body_lines for note in PATCH_NOTES)


def _patch_engine(*, announced, realms):
    engine = MagicMock()
    engine.announced_patch_names.return_value = announced
    engine.realms_to_announce.return_value = realms
    return engine


@pytest.mark.asyncio
async def test_announce_pending_posts_then_marks():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])
    posted: list[tuple[int, str]] = []

    async def _fake_post(bot, realm_id, text, *, reply_markup=None):
        posted.append((int(realm_id), text))
        return FanoutResult(ok=True, targets=1, sent=1)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_fake_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    assert [r for r, _ in posted] == [1, 2]
    assert all("Вестник долины" in t for _, t in posted)
    engine.mark_patch_announced.assert_called_once_with(note.id)


@pytest.mark.asyncio
async def test_announce_pending_skips_already_announced():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced={note.id}, realms=[{"id": 1}])
    post = AsyncMock(return_value=FanoutResult(ok=True, targets=1))

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    post.assert_not_called()
    engine.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_defers_mark_when_all_sends_fail():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])

    async def _fail_post(bot, realm_id, text, *, reply_markup=None):
        return FanoutResult(ok=False, targets=2, sent=0)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_fail_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    engine.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_marks_when_at_least_one_realm_ok():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])

    async def _partial_post(bot, realm_id, text, *, reply_markup=None):
        if int(realm_id) == 2:
            return FanoutResult(ok=True, targets=1, sent=1)
        return FanoutResult(ok=False, targets=1, sent=0)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_partial_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    engine.mark_patch_announced.assert_called_once_with(note.id)


def test_should_mark_patch_announced_predicates():
    assert should_mark_patch_announced(
        realm_count=0, populated=0, ok_count=0, hard_fails=0
    )
    assert should_mark_patch_announced(
        realm_count=2, populated=0, ok_count=0, hard_fails=0
    )
    assert should_mark_patch_announced(
        realm_count=2, populated=2, ok_count=1, hard_fails=0
    )
    assert not should_mark_patch_announced(
        realm_count=2, populated=2, ok_count=0, hard_fails=0
    )
    assert not should_mark_patch_announced(
        realm_count=2, populated=0, ok_count=0, hard_fails=2
    )


@pytest.mark.asyncio
async def test_announce_marks_on_partial_realm_delivery():
    """Заблокированный получатель не должен крутить вестник вечно."""
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}])

    async def _partial(bot, realm_id, text, *, reply_markup=None):
        return FanoutResult(ok=False, targets=3, sent=2)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_partial),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    engine.mark_patch_announced.assert_called_once_with(note.id)


@pytest.mark.asyncio
async def test_announce_defers_on_hard_fanout_failure():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])

    async def _hard_fail(bot, realm_id, text, *, reply_markup=None):
        return FanoutResult(ok=False, targets=0, sent=0)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_hard_fail),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    engine.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_defers_when_only_empty_realms_succeed():
    """Пустая долина не должна закрывать патч, если населённые упали."""
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])

    async def _empty_and_fail(bot, realm_id, text, *, reply_markup=None):
        if int(realm_id) == 1:
            return FanoutResult(ok=True, targets=0, sent=0)
        return FanoutResult(ok=False, targets=3, sent=0)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_empty_and_fail),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    engine.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_marks_when_all_realms_empty():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[{"id": 1}, {"id": 2}])

    async def _empty_post(bot, realm_id, text, *, reply_markup=None):
        return FanoutResult(ok=True, targets=0, sent=0)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_empty_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    engine.mark_patch_announced.assert_called_once_with(note.id)


@pytest.mark.asyncio
async def test_announce_marks_when_no_realms():
    note = PATCH_NOTES[0]
    engine = _patch_engine(announced=set(), realms=[])
    post = AsyncMock(return_value=FanoutResult(ok=True, targets=1))

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    post.assert_not_called()
    engine.mark_patch_announced.assert_called_once_with(note.id)


def test_db_mark_and_list_announced_patches_sql():
    from app.database import Database

    db = Database(connect=False)
    db.connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = [("raid_victim_shield_one_tick_v1",)]
    db.cursor = cursor

    names = db.list_announced_patch_names()
    assert names == {"raid_victim_shield_one_tick_v1"}
    assert "announced_patches" in cursor.execute.call_args_list[0][0][0]

    db.mark_patch_announced("raid_victim_shield_one_tick_v1")
    sql = cursor.execute.call_args_list[-1][0][0]
    assert "INSERT INTO announced_patches" in sql
    assert "ON CONFLICT" in sql
    db.connection.commit.assert_called()
