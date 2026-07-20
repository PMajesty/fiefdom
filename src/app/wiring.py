"""Composition root: сборка Database, Engine и сервисов.

Хендлеры/планировщик берут только get_engine().
Сервисы живут на Engine (собираются в Engine.__init__).
Прямой доступ к БД и приватным членам Engine отсюда не раздаётся.
"""
from __future__ import annotations

from app.database import Database, get_db
from app.engine import Engine

_engine: Engine | None = None


def build_app(*, db: Database | None = None) -> Engine:
    """Собирает приложение: Database + Engine со всеми сервисами."""
    database = db if db is not None else get_db()
    return Engine(database)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = build_app()
    return _engine
