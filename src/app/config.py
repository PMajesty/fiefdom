from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
POE_API_KEY = os.getenv("POE_API_KEY", "")
POE_BASE_URL = os.getenv("POE_BASE_URL", "https://api.poe.com/v1").rstrip("/")
POE_NARRATIVE_MODEL = os.getenv("POE_NARRATIVE_MODEL", "Gemini-3.1-Flash-Lite")
POE_AGENT_MAX_RETRIES = int(os.getenv("POE_AGENT_MAX_RETRIES", "3"))
POE_AGENT_RETRY_DELAY = float(os.getenv("POE_AGENT_RETRY_DELAY", "2.0"))

DB_CONFIG = {
    "NAME": os.getenv("DB_NAME", "fiefdom"),
    "USER": os.getenv("DB_USER", "fiefdom_bot"),
    "PASSWORD": os.getenv("DB_PASSWORD", ""),
    "HOST": os.getenv("DB_HOST", "localhost"),
    "PORT": os.getenv("DB_PORT", "5432"),
}

TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
TICK_HOUR = int(os.getenv("TICK_HOUR", "13"))
TICK_MINUTE = int(os.getenv("TICK_MINUTE", "0"))
TICK_HOUR_2 = int(os.getenv("TICK_HOUR_2", "19"))
TICK_MINUTE_2 = int(os.getenv("TICK_MINUTE_2", "0"))


def tick_slots() -> list[tuple[int, int]]:
    """Слоты дневного тика в локальном времени долины."""
    return [
        (TICK_HOUR, TICK_MINUTE),
        (TICK_HOUR_2, TICK_MINUTE_2),
    ]

_raw_admin = os.getenv("ADMIN_USER_ID", "").strip()
ADMIN_USER_ID = int(_raw_admin) if _raw_admin else None

LOG_DIRECTORY = os.getenv("LOG_DIR") or str(PROJECT_ROOT / "logs")

# Префикс групповых команд, чтобы не пересекаться с другими ботами в чате.
CMD_PREFIX = "вч_"
CMD_PREFIX_LATIN = "vch_"
