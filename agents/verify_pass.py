"""Pass 4: Behavioral verification of applied fixes.

For non-principles issues, runs the `behavioral_probe` scenario against the
original and fixed prompts, then an independent judge scores the delta on a
1–10 scale with a 4-way verdict category. Principles-dimension issues skip
the probe and rely on the structural assertion alone.

The judge is **channel-agnostic** — it sees only the two behaviours +
expected change, never the prompt text. This keeps the judge from rewarding
a fix just because the prompt looks improved.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from core.models import FixProposal, JudgeRaw, PrinciplesBrief, VerificationResult

from .fix_engine import extract_key_phrases
from .llm import llm
from .principles_pass import format_brief_for_passes
from .prompts import FOLLOWUP_GENERATOR_PROMPT, JUDGE_PROMPT, PROBE_PROMPT

logger = logging.getLogger(__name__)

# Judge score (1-10) that marks a fix as a clear behavioral improvement.
# Scores 4-6 are "partial" and do NOT count as behavioral_pass.
BEHAVIORAL_PASS_THRESHOLD = 7


async def _agenerate_followup(
    probe_context: str,
    agent_reply: str,
    issue_description: str,
    prior_turns: list[tuple[str, str]],
) -> str:
    transcript = "\n\n".join(
        f"CALLER: {u}\nAGENT: {a}" for u, a in prior_turns
    )
    user = (
        f"<issue_being_tested>\n{issue_description}\n</issue_being_tested>\n\n"
        f"<original_scenario>\n{probe_context}\n</original_scenario>\n\n"
        f"<conversation_so_far>\n{transcript}\n</conversation_so_far>\n\n"
        "Write the caller's next message."
    )
    try:
        out = await llm.acall(FOLLOWUP_GENERATOR_PROMPT, user, max_tokens=300)
        return out.strip()
    except Exception as err:
        logger.warning("Follow-up generator failed: %s", err)
        return "(follow-up could not be generated)"


async def _arun_probe_trace(
    system_prompt: str,
    initial_user: str,
    num_turns: int,
    issue_id: str,
    proposal: FixProposal,
) -> str:
    """Async version of `_run_probe_trace`. Turns remain sequential within a trace."""
    first_reply = await llm.acall(system_prompt, initial_user)
    if num_turns <= 1:
        return first_reply

    turns: list[tuple[str, str]] = [(initial_user, first_reply)]
    trace_lines = [f"--- Turn 1 ---\nCALLER: {initial_user}\nAGENT: {first_reply}"]

    issue_description = (
        f"{proposal.fix_description} — root cause: {proposal.root_cause}. "
        f"Assertion: {proposal.assertion}"
    )

    for turn_idx in range(2, num_turns + 1):
        followup = await _agenerate_followup(
            proposal.behavioral_probe, first_reply, issue_description, turns
        )
        try:
            reply = await llm.acall(system_prompt, followup)
        except Exception as err:
            logger.warning(
                "Probe turn %d failed for %s: %s", turn_idx, issue_id, err
            )
            break
        turns.append((followup, reply))
        trace_lines.append(
            f"--- Turn {turn_idx} ---\nCALLER: {followup}\nAGENT: {reply}"
        )
        first_reply = reply

    return "\n\n".join(trace_lines)


async def averify(
    issue_id: str,
    proposal: FixProposal,
    original_prompt: str,
    fixed_prompt: str,
    brief: Optional[PrinciplesBrief] = None,
    num_turns: int = 1,
) -> VerificationResult:
    """Async verify: runs orig+fixed probes in parallel, then the judge."""

    key_phrases = extract_key_phrases(proposal.assertion)
    structural_pass = any(
        phrase.lower() in fixed_prompt.lower() for phrase in key_phrases
    )
    if not structural_pass and proposal.new_content[:80] in fixed_prompt:
        structural_pass = True

    if proposal.dimension == "principles":
        return VerificationResult(
            issue_id=issue_id,
            structural_pass=structural_pass,
            behavioral_pass=structural_pass,
            improvement_score=8 if structural_pass else 2,
            verdict_category="improved" if structural_pass else "unchanged",
            explanation=(
                "Principles issue verified structurally via assertion. "
                "No behavioral probe applicable for this principle."
            ),
            original_probe_response="N/A — principles issue, probe skipped.",
            fixed_probe_response="N/A — principles issue, probe skipped.",
            remaining_concerns=None,
        )

    probe_context = proposal.behavioral_probe
    brief_block = format_brief_for_passes(brief)

    def _probe_system(system_prompt: str) -> str:
        parts = [PROBE_PROMPT]
        if brief_block:
            parts.append(brief_block)
        parts.append(f"<system_prompt>\n{system_prompt}\n</system_prompt>")
        return "\n\n".join(parts)

    initial_user = (
        f"SCENARIO (mid-workflow state):\n{probe_context}\n\n"
        f"What do you do next? Respond with SPEECH, TOOL_CALLS, and CONDITIONS_CHECKED."
    )

    orig_task = asyncio.create_task(
        _arun_probe_trace(
            _probe_system(original_prompt), initial_user, num_turns, issue_id, proposal
        )
    )
    fixed_task = asyncio.create_task(
        _arun_probe_trace(
            _probe_system(fixed_prompt), initial_user, num_turns, issue_id, proposal
        )
    )
    orig_response, fixed_response = await asyncio.gather(orig_task, fixed_task)

    judge_input = json.dumps(
        {
            "issue": {
                "id": issue_id,
                "title": proposal.fix_description,
                "root_cause": proposal.root_cause,
                "impact_if_unfixed": proposal.impact_if_unfixed,
            },
            "expected_change": proposal.assertion,
            "scenario": probe_context,
            "original_behavior": orig_response,
            "fixed_behavior": fixed_response,
        },
        indent=2,
    )
    verdict_data = await llm.acall_json(JUDGE_PROMPT, judge_input, JudgeRaw)

    behavioral_pass = (
        verdict_data.verdict == "improved"
        and verdict_data.improvement_score >= BEHAVIORAL_PASS_THRESHOLD
    )

    return VerificationResult(
        issue_id=issue_id,
        structural_pass=structural_pass,
        behavioral_pass=behavioral_pass,
        improvement_score=verdict_data.improvement_score,
        verdict_category=verdict_data.verdict,
        explanation=verdict_data.explanation,
        original_probe_response=orig_response,
        fixed_probe_response=fixed_response,
        remaining_concerns=verdict_data.remaining_concerns,
    )


def verify(
    issue_id: str,
    proposal: FixProposal,
    original_prompt: str,
    fixed_prompt: str,
    brief: Optional[PrinciplesBrief] = None,
    num_turns: int = 1,
) -> VerificationResult:
    """Sync shim — delegates to `averify` via asyncio.run for legacy callers."""
    return asyncio.run(
        averify(issue_id, proposal, original_prompt, fixed_prompt, brief, num_turns)
    )
