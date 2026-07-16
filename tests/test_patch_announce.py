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
from app.patch_announce import announce_pending_patches


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


@pytest.mark.asyncio
async def test_announce_pending_posts_then_marks():
    note = PATCH_NOTES[0]
    db = MagicMock()
    db.list_announced_patch_names.return_value = set()
    db.list_realms.return_value = [{"id": 1}, {"id": 2}]
    engine = MagicMock()
    engine.db = db
    posted: list[tuple[int, str]] = []

    async def _fake_post(bot, realm_id, text, *, reply_markup=None):
        posted.append((int(realm_id), text))
        return True

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_fake_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    assert [r for r, _ in posted] == [1, 2]
    assert all("Вестник долины" in t for _, t in posted)
    db.mark_patch_announced.assert_called_once_with(note.id)


@pytest.mark.asyncio
async def test_announce_pending_skips_already_announced():
    note = PATCH_NOTES[0]
    db = MagicMock()
    db.list_announced_patch_names.return_value = {note.id}
    db.list_realms.return_value = [{"id": 1}]
    engine = MagicMock()
    engine.db = db
    post = AsyncMock(return_value=True)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    post.assert_not_called()
    db.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_defers_mark_when_all_sends_fail():
    note = PATCH_NOTES[0]
    db = MagicMock()
    db.list_announced_patch_names.return_value = set()
    db.list_realms.return_value = [{"id": 1}, {"id": 2}]
    engine = MagicMock()
    engine.db = db

    async def _fail_post(bot, realm_id, text, *, reply_markup=None):
        return False

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_fail_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == []
    db.mark_patch_announced.assert_not_called()


@pytest.mark.asyncio
async def test_announce_marks_when_at_least_one_realm_ok():
    note = PATCH_NOTES[0]
    db = MagicMock()
    db.list_announced_patch_names.return_value = set()
    db.list_realms.return_value = [{"id": 1}, {"id": 2}]
    engine = MagicMock()
    engine.db = db

    async def _partial_post(bot, realm_id, text, *, reply_markup=None):
        return int(realm_id) == 2

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=_partial_post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    db.mark_patch_announced.assert_called_once_with(note.id)


@pytest.mark.asyncio
async def test_announce_marks_when_no_realms():
    note = PATCH_NOTES[0]
    db = MagicMock()
    db.list_announced_patch_names.return_value = set()
    db.list_realms.return_value = []
    engine = MagicMock()
    engine.db = db
    post = AsyncMock(return_value=True)

    with (
        patch("app.patch_announce.get_engine", return_value=engine),
        patch("app.patch_announce.post_realm_public", new=post),
        patch.object(notes_mod, "PATCH_NOTES", (note,)),
    ):
        delivered = await announce_pending_patches(object())

    assert delivered == [note.id]
    post.assert_not_called()
    db.mark_patch_announced.assert_called_once_with(note.id)


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
