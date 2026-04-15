"""Pass 2: Propose anchor-based fixes with assertions and behavioral probes.

Accepts optional `retry_feedback` (fed back from prior-iteration judge
verdicts) and optional `lessons` (consolidated rules from past runs) — both
are injected into the user content as separate XML blocks so the model can
react to them explicitly.
"""

from __future__ import annotations

import json
from typing import Optional

from models import AnalysisResult, Issue, PrinciplesBrief
from principles import CANONICAL_PRINCIPLES_TEXT

from .llm import _cached_block, llm
from .principles_pass import format_brief_for_passes
from .prompts import ANALYSIS_PROMPT


def _analysis_system() -> list[dict]:
    return [
        _cached_block(
            "<canonical_principles>\n"
            + CANONICAL_PRINCIPLES_TEXT
            + "\n</canonical_principles>\n\n"
            + ANALYSIS_PROMPT
        ),
    ]


def analyze(
    prompt_text: str,
    issues: list[Issue],
    brief: Optional[PrinciplesBrief] = None,
    retry_feedback: Optional[str] = None,
    lessons: str = "",
) -> AnalysisResult:
    """Propose anchor-based fixes; optionally steered by past-run lessons + retry feedback."""
    issues_json = json.dumps([i.model_dump() for i in issues], indent=2)
    user_content = (
        f"<prompt>\n{prompt_text}\n</prompt>"
        f"\n\n<issues>\n{issues_json}\n</issues>"
    )
    brief_block = format_brief_for_passes(brief)
    if brief_block:
        user_content += f"\n\n{brief_block}"
    if lessons:
        user_content += f"\n\n{lessons}"
    if retry_feedback:
        user_content += (
            "\n\n<prior_attempt_feedback>\n"
            "Your previous fix proposals for these issues did not fully resolve them. "
            "Review the verdicts below and propose DIFFERENT fixes — change the "
            "anchor, reshape new_content to address what the judge flagged, or "
            "design a sharper adversarial behavioral_probe if the verdict was "
            "'inconclusive' (meaning the prior probe didn't exercise the bug).\n\n"
            f"{retry_feedback}\n"
            "</prior_attempt_feedback>"
        )
    result = llm.call_json(
        _analysis_system(), user_content, AnalysisResult, max_tokens=32000
    )

    # Post-fill dimension from the source Issue so verify-routing is robust.
    issue_dimension = {i.id: i.dimension for i in issues}
    for proposal in result.proposals:
        if proposal.dimension is None:
            proposal.dimension = issue_dimension.get(proposal.issue_id)
    return result
