"""Постоянная поверхность контракт-сканов: Engine + пакет services."""
from __future__ import annotations

import inspect
from pathlib import Path

from app.engine import Engine

_SERVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "app" / "services"


def live_path_source() -> str:
    chunks = [inspect.getsource(Engine)]
    for path in sorted(_SERVICES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n".join(chunks)
