"""Устав уходит .txt-документом, не HTML-сообщением."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.guide import game_guide
from app.messaging import (
    GUIDE_DOCUMENT_CAPTION,
    GUIDE_DOCUMENT_FILENAME,
    TELEGRAM_MESSAGE_LIMIT,
    UTF8_BOM,
    reply_guide_document,
)


def test_game_guide_is_plain_and_long():
    text = game_guide()
    assert len(text) > TELEGRAM_MESSAGE_LIMIT
    assert "<b>" not in text
    assert "</b>" not in text
    assert text.startswith("📜 Краткий устав")


@pytest.mark.asyncio
async def test_reply_guide_document_sends_utf8_txt_with_bom():
    message = MagicMock()
    message.answer_document = AsyncMock()
    text = game_guide()

    await reply_guide_document(message, text, reply_markup="kb")

    message.answer_document.assert_awaited_once()
    args, kwargs = message.answer_document.await_args
    document = args[0]
    assert document.filename == GUIDE_DOCUMENT_FILENAME
    assert document.data.startswith(UTF8_BOM)
    assert document.data == UTF8_BOM + text.encode("utf-8")
    assert document.data.decode("utf-8-sig") == text
    assert kwargs["caption"] == GUIDE_DOCUMENT_CAPTION
    assert kwargs["reply_markup"] == "kb"
    assert "parse_mode" not in kwargs
