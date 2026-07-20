from __future__ import annotations

from app.database import get_db
from app.engine import Engine

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine(get_db())
    return _engine
