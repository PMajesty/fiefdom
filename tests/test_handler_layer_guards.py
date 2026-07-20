"""Ratchet: хендлеры не обходят публичный API Engine через engine.db."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = ROOT / "src" / "app" / "handlers"

OCCURRENCE_RE = re.compile(r"\bengine\.db\b|\beng\.db\b|\bengine\._[a-zA-Z_]+")
# Конец RHS: bare / скобки / точка с запятой; не method-call после .db
ALIAS_RE = re.compile(
    r"=\s*(?:\(\s*)?(?:engine|eng)\.db\s*(?:\)\s*)?(?:#|;|$)"
)

# Известный долг; любой другой .py в scope без записи = 0.
OCCURRENCE_FREEZE: dict[str, int] = {
    "src/app/handlers/dm.py": 0,
    "src/app/handlers/callbacks.py": 0,
    "src/app/handlers/admin.py": 0,
    "src/app/handlers/group.py": 0,
    "src/app/handlers/shared.py": 0,
    "src/app/scheduler.py": 0,
    "src/app/patch_announce.py": 0,
    "src/app/notifier.py": 0,
    "src/app/wiring.py": 0,
    "src/app/messaging.py": 0,
}

ALIAS_FREEZE: dict[str, int] = {}

INFRA_FILES = (
    "src/app/scheduler.py",
    "src/app/patch_announce.py",
    "src/app/notifier.py",
    "src/app/wiring.py",
    "src/app/messaging.py",
)

_HANDLERS_IMPORT_RE = re.compile(
    r"(?:from app\.handlers(?:\s|\.|$)|import app\.handlers\b|from app import handlers\b)"
)


def _scoped_rels() -> list[str]:
    rels = {p.relative_to(ROOT).as_posix() for p in HANDLERS_DIR.glob("*.py")}
    rels.update(INFRA_FILES)
    rels.update(OCCURRENCE_FREEZE)
    rels.update(ALIAS_FREEZE)
    return sorted(rels)


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"Scoped file missing: {rel}"
    return path.read_text(encoding="utf-8")


def _occurrence_hits(rel: str, text: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for _ in OCCURRENCE_RE.finditer(line):
            hits.append(f"{rel}:{lineno}: {line}")
    return hits


def _alias_hits(rel: str, text: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALIAS_RE.search(line):
            hits.append(f"{rel}:{lineno}: {line}")
    return hits


def test_engine_db_occurrence_freeze():
    violations: list[str] = []
    for rel in _scoped_rels():
        expected = OCCURRENCE_FREEZE.get(rel, 0)
        text = _read(rel)
        hits = _occurrence_hits(rel, text)
        actual = len(hits)
        if actual != expected:
            hit_list = "\n".join(hits) if hits else "(none)"
            violations.append(
                f"{rel}: expected={expected} actual={actual}\n{hit_list}"
            )
    assert not violations, "engine.db occurrence freeze broken:\n" + "\n\n".join(
        violations
    )


def test_no_new_engine_db_aliases():
    violations: list[str] = []
    for rel in _scoped_rels():
        expected = ALIAS_FREEZE.get(rel, 0)
        text = _read(rel)
        hits = _alias_hits(rel, text)
        actual = len(hits)
        if actual != expected:
            hit_list = "\n".join(hits) if hits else "(none)"
            violations.append(
                f"{rel}: expected={expected} actual={actual}\n{hit_list}"
            )
    assert not violations, "engine.db alias freeze broken:\n" + "\n\n".join(
        violations
    )


def test_infra_must_not_import_handlers():
    violations: list[str] = []
    for rel in INFRA_FILES:
        text = _read(rel)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _HANDLERS_IMPORT_RE.search(line):
                violations.append(f"{rel}:{lineno}: {line}")
    assert not violations, "infra must not import handlers:\n" + "\n".join(
        violations
    )


# Приватные хелперы Database; снаружи только публичные методы.
# Ratchet по частым формам (self._db._* / db._* / engine.db._*);
# не ловит alias/getattr; комментарии после # вне кавычек игнорируются.
_DB_PRIVATE_RE = re.compile(
    r"(?:self\._db|engine\.db|eng\.db|(?<![\w.])db)\._[a-zA-Z_]\w*"
)
_APP_SRC = ROOT / "src" / "app"
_DB_PRIVATE_ALLOW = frozenset({"src/app/database.py"})


def _code_without_comment(line: str) -> str:
    """Убирает # комментарий вне кавычек (упрощённо для ratchet)."""
    in_squote = False
    in_dquote = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_dquote:
            in_squote = not in_squote
        elif ch == '"' and not in_squote:
            in_dquote = not in_dquote
        elif ch == "#" and not in_squote and not in_dquote:
            return line[:i]
    return line


def test_no_private_database_access_outside_database():
    violations: list[str] = []
    for path in sorted(_APP_SRC.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in _DB_PRIVATE_ALLOW:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = _code_without_comment(line)
            if _DB_PRIVATE_RE.search(code):
                violations.append(f"{rel}:{lineno}: {line}")
    assert not violations, "db._* outside database.py:\n" + "\n".join(violations)

