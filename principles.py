"""Loads the canonical principles library from docs/principles.md."""

from pathlib import Path

_PRINCIPLES_PATH = Path(__file__).parent / "docs" / "principles.md"

CANONICAL_PRINCIPLES_TEXT: str = _PRINCIPLES_PATH.read_text(encoding="utf-8")
