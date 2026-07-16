"""Точка входа Telegram-бота Вотчина."""
from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import LOG_DIRECTORY, TELEGRAM_BOT_TOKEN
from app.database import init_db
from app.handlers import admin, callbacks, dm, group
from app.scheduler import scheduler_loop


def setup_logging() -> None:
    log_dir = Path(LOG_DIRECTORY)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "fiefdom.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = RotatingFileHandler(
        log_file,
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)


async def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан")
        raise SystemExit(1)

    init_db()
    logger.info("Database initialized")

    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(group.router)
    dp.include_router(dm.router)
    dp.include_router(callbacks.router)
    dp.include_router(admin.router)

    stop_event = asyncio.Event()
    sched_task = asyncio.create_task(scheduler_loop(bot, stop_event), name="scheduler")

    try:
        logger.info("Polling started")
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        sched_task.cancel()
        try:
            await sched_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
