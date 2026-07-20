"""Постоянная поверхность контракт-сканов: Engine + пакет services.

Проверка вызовов - AST (Attribute + Call), не текстовый поиск подстроки:
комментарии, строки и мёртвые хелперы не дают ложный green.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.engine import Engine

_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _ROOT / "src" / "app" / "services"


def _live_path_files() -> list[tuple[str, str]]:
    """[(метка, исходник), ...] для Engine и app/services/*.py."""
    chunks: list[tuple[str, str]] = [
        ("app.engine.Engine", inspect.getsource(Engine)),
    ]
    for path in sorted(_SERVICES_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        chunks.append(
            (
                path.relative_to(_ROOT).as_posix(),
                path.read_text(encoding="utf-8"),
            )
        )
    return chunks


def live_path_source() -> str:
    """Склейка исходников (отладка / редкие текстовые проверки)."""
    return "\n".join(src for _, src in _live_path_files())


def live_path_calls_method(method: str) -> bool:
    """True, если на live-пути есть вызов obj.<method>(...)."""
    for label, src in _live_path_files():
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            raise AssertionError(f"live path AST parse failed: {label}: {exc}") from exc
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == method:
                return True
    return False
