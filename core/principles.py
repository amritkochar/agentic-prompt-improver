"""Loads the canonical principles library from docs/principles.md."""

from pathlib import Path

# docs/principles.md is one level up from this file (core/) and into docs/
_PRINCIPLES_PATH = Path(__file__).parent.parent / "docs" / "principles.md"

CANONICAL_PRINCIPLES_TEXT: str = _PRINCIPLES_PATH.read_text(encoding="utf-8")
