"""Pass 1: Issue detection with a reflection/critique pass.

Detection scans the prompt for issues across three dimensions
(caller-experience, workflow-adherence, principles). A second reflection
call prunes false positives and adds any obvious high-severity issues the
first pass missed. Both calls share the canonical-principles block via
prompt caching.
"""

from __future__ import annotations

from typing import Optional

from core.models import DetectionResult, PrinciplesBrief
from core.principles import CANONICAL_PRINCIPLES_TEXT

from .llm import _cached_block, llm
from .principles_pass import format_brief_for_passes
from .prompts import DETECTION_PROMPT, REFLECTION_PROMPT


def _detection_system() -> list[dict]:
    return [
        _cached_block(
            "<canonical_principles>\n"
            + CANONICAL_PRINCIPLES_TEXT
            + "\n</canonical_principles>\n\n"
            + DETECTION_PROMPT
        ),
    ]


def _reflection_system() -> list[dict]:
    return [
        _cached_block(
            "<canonical_principles>\n"
            + CANONICAL_PRINCIPLES_TEXT
            + "\n</canonical_principles>\n\n"
            + REFLECTION_PROMPT
        ),
    ]


def detect(
    prompt_text: str,
    tools_json: Optional[str] = None,
    brief: Optional[PrinciplesBrief] = None,
) -> DetectionResult:
    """Detect quality issues in the prompt, then self-critique via reflection."""
    brief_block = format_brief_for_passes(brief)

    user_content = f"<prompt>\n{prompt_text}\n</prompt>"
    if tools_json:
        user_content += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"
    if brief_block:
        user_content += f"\n\n{brief_block}"

    raw = llm.call_json(
        _detection_system(), user_content, DetectionResult, max_tokens=32000
    )

    reflection_input = (
        f"<detected_issues>\n{raw.model_dump_json(indent=2)}\n</detected_issues>"
        f"\n\n<original_prompt>\n{prompt_text}\n</original_prompt>"
    )
    if tools_json:
        reflection_input += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"
    if brief_block:
        reflection_input += f"\n\n{brief_block}"

    refined = llm.call_json(
        _reflection_system(), reflection_input, DetectionResult, max_tokens=32000
    )
    return refined
