"""LLM-нарратив событий через Poe (OpenAI-compatible API)."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import aiohttp

from app.config import (
    POE_AGENT_MAX_RETRIES,
    POE_AGENT_RETRY_DELAY,
    POE_API_KEY,
    POE_BASE_URL,
    POE_NARRATIVE_MODEL,
)

logger = logging.getLogger(__name__)

_DIGIT_RE = re.compile(r"\d+")
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

_MIN_NARRATIVE_LEN = 40
_MAX_NARRATIVE_LEN = 1200

_SYSTEM_PROMPT = (
    "Ты злой средневековый хронист долины. Пиши по-русски, сухо, язвительно, без героики.\n"
    "Верни ТОЛЬКО валидный JSON без пояснений и без markdown:\n"
    '{"narrative":"...","button_labels":{"key":"подпись"}}\n'
    "narrative — 2–4 предложения про событие из brief.\n"
    "button_labels — подписи кнопок; ключи бери только из brief (если есть).\n"
    "СТРОГО ЗАПРЕЩЕНО выдумывать числа: любые цифры в тексте должны совпадать "
    "с числами из brief. Если числа нет в brief — не пиши цифры."
)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=45)
_RETRYABLE_HTTP = {408, 429, 500, 502, 503, 504}


def _walk_numbers(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _walk_numbers(item, out)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            _walk_numbers(item, out)
    elif isinstance(value, bool):
        return
    elif isinstance(value, int):
        out.add(str(value))
    elif isinstance(value, float):
        out.add(str(value))
        if value.is_integer():
            out.add(str(int(value)))
    elif isinstance(value, str):
        out.update(_DIGIT_RE.findall(value))


def _allowed_numbers_from_brief(brief: dict) -> set[str]:
    allowed: set[str] = set()
    _walk_numbers(brief, allowed)
    return allowed


def validate_narrative(narrative: str, allowed_numbers: set[str]) -> bool:
    if not isinstance(narrative, str):
        return False
    text = narrative.strip()
    if len(text) < _MIN_NARRATIVE_LEN or len(text) > _MAX_NARRATIVE_LEN:
        return False
    for token in _DIGIT_RE.findall(text):
        if token not in allowed_numbers:
            return False
    return True


def _extract_json_object(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def _normalize_button_labels(raw: Any) -> dict[str, str] | None:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    labels: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return None
        labels[key] = value.strip()
    return labels


async def _chat_completion(brief: dict) -> str | None:
    if not POE_API_KEY:
        logger.warning("generate_event_text: POE_API_KEY пуст")
        return None

    payload = {
        "model": POE_NARRATIVE_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "brief события (JSON):\n" + json.dumps(brief, ensure_ascii=False),
            },
        ],
        "stream": False,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {POE_API_KEY}",
        "Content-Type": "application/json",
        "Accept-Encoding": "gzip, deflate",
    }
    url = f"{POE_BASE_URL}/chat/completions"
    last_error: Exception | None = None

    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        for attempt in range(POE_AGENT_MAX_RETRIES + 1):
            if attempt > 0:
                delay = POE_AGENT_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Poe narrative: повтор %s/%s через %.1fs (%s)",
                    attempt,
                    POE_AGENT_MAX_RETRIES,
                    delay,
                    last_error,
                )
                await asyncio.sleep(delay)
            try:
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        last_error = RuntimeError(f"Poe API {response.status}: {error_text}")
                        if response.status not in _RETRYABLE_HTTP or attempt >= POE_AGENT_MAX_RETRIES:
                            logger.error("%s", last_error)
                            return None
                        continue
                    data = await response.json()
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
                    last_error = RuntimeError("Poe API: пустой content")
                    if attempt >= POE_AGENT_MAX_RETRIES:
                        logger.error("%s", last_error)
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                last_error = exc
                if attempt >= POE_AGENT_MAX_RETRIES:
                    logger.error("Poe narrative request failed: %s", exc)
                    return None
    return None


async def generate_event_text(brief: dict) -> dict | None:
    """Генерирует narrative + button_labels. При любой ошибке — None (caller → canned)."""
    try:
        if not isinstance(brief, dict):
            return None
        raw = await _chat_completion(brief)
        if not raw:
            return None
        parsed = _extract_json_object(raw)
        if not parsed:
            logger.warning("generate_event_text: не удалось разобрать JSON")
            return None
        narrative = parsed.get("narrative")
        if not isinstance(narrative, str):
            return None
        narrative = narrative.strip()
        allowed = _allowed_numbers_from_brief(brief)
        if not validate_narrative(narrative, allowed):
            logger.warning("generate_event_text: narrative не прошёл валидацию")
            return None
        labels = _normalize_button_labels(parsed.get("button_labels"))
        if labels is None:
            logger.warning("generate_event_text: button_labels невалидны")
            return None
        return {"narrative": narrative, "button_labels": labels}
    except Exception as exc:
        logger.error("generate_event_text: %s", exc)
        return None
