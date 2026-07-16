"""Cop: запрет em dash и кавычек-ёлочек в исходниках."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (ROOT / "src", ROOT / "tests")
SCAN_FILES = (ROOT / "README.md",)
FORBIDDEN = {
    "\u2014": "em dash (U+2014); use ASCII hyphen-minus (-)",
    "\u00ab": "left guillemet (U+00AB); use regular quotes (\")",
    "\u00bb": "right guillemet (U+00BB); use regular quotes (\")",
}


def _iter_text_files() -> list[Path]:
    files: list[Path] = []
    for directory in SCAN_DIRS:
        if not directory.is_dir():
            continue
        files.extend(directory.rglob("*.py"))
        files.extend(directory.rglob("*.md"))
    for path in SCAN_FILES:
        if path.is_file():
            files.append(path)
    return sorted({p.resolve() for p in files})


def test_no_em_dashes_or_guillemets():
    violations: list[str] = []
    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for char, hint in FORBIDDEN.items():
                if char not in line:
                    continue
                col = line.index(char) + 1
                violations.append(f"{rel}:{lineno}:{col}: {hint}")
    assert not violations, "Forbidden typography:\n" + "\n".join(violations)
