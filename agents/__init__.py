"""Public surface of the agents package.

Re-exports each pass as a flat name so callers can keep writing
`agents.detect(...)`, `agents.analyze(...)`, etc., without caring about the
internal module layout.
"""

from __future__ import annotations

from .analyze_pass import analyze
from .detect_pass import detect
from .fix_engine import apply_fixes
from .llm import HAIKU_MODEL, LLM, llm
from .memory_pass import consolidate
from .principles_pass import establish_principles, format_brief_for_passes
from .verify_pass import BEHAVIORAL_PASS_THRESHOLD, averify, verify

__all__ = [
    "LLM",
    "llm",
    "HAIKU_MODEL",
    "BEHAVIORAL_PASS_THRESHOLD",
    "establish_principles",
    "format_brief_for_passes",
    "detect",
    "analyze",
    "apply_fixes",
    "verify",
    "averify",
    "consolidate",
]
