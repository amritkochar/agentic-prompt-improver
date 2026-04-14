"""All LLM agent passes: detect, reflect, analyze, fix, validate, verify."""

from __future__ import annotations

import difflib
import json
import re
from typing import Optional, Union

import anthropic

from models import (
    ActivePrinciple,
    AnalysisResult,
    AssertionCheck,
    DetectionResult,
    FixProposal,
    FixValidation,
    Issue,
    JudgeRaw,
    PrinciplesBrief,
    VerificationResult,
)
from loader import KNOWN_KEYS
from principles import CANONICAL_PRINCIPLES_TEXT


SystemPrompt = Union[str, list[dict]]


# ---------------------------------------------------------------------------
# LLM wrapper
# ---------------------------------------------------------------------------

def _cached_block(text: str) -> dict:
    """Wrap static text as a cache_control ephemeral block for prompt caching."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def _append_to_system(system: SystemPrompt, suffix: str) -> SystemPrompt:
    """Append a (non-cached) suffix to a system prompt without breaking the prefix cache.

    If system is a string, concatenate. If it's a list of blocks, append an
    uncached trailing text block so the cache prefix is preserved.
    """
    if isinstance(system, str):
        return system + suffix
    return [*system, {"type": "text", "text": suffix}]


def _empty_model_stats() -> dict:
    return {
        "calls": 0,
        "input": 0,
        "cache_read": 0,
        "cache_create": 0,
        "output": 0,
    }


class LLM:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic()
        self.model = model
        # Per-model running totals. Each model_id maps to a dict of counters.
        self.stats: dict[str, dict] = {}

    def _record(self, model: str, usage) -> None:
        """Merge the usage metrics from one response into self.stats."""
        entry = self.stats.setdefault(model, _empty_model_stats())
        entry["calls"] += 1
        entry["input"] += getattr(usage, "input_tokens", 0) or 0
        entry["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        entry["cache_create"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        entry["output"] += getattr(usage, "output_tokens", 0) or 0

    def snapshot(self) -> dict:
        """Deep copy of current stats for per-pass deltas."""
        return {m: dict(v) for m, v in self.stats.items()}

    @staticmethod
    def delta(after: dict, before: dict) -> dict:
        """Compute after - before across all models."""
        out: dict[str, dict] = {}
        for model in set(after) | set(before):
            a = after.get(model, _empty_model_stats())
            b = before.get(model, _empty_model_stats())
            row = {k: a.get(k, 0) - b.get(k, 0) for k in _empty_model_stats()}
            if row["calls"] > 0:
                out[model] = row
        return out

    def call(
        self,
        system: SystemPrompt,
        user: str,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> str:
        """Plain text response. `system` may be a str or a list of content blocks."""
        model_id = model or self.model
        response = self.client.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        self._record(model_id, response.usage)
        return response.content[0].text

    def call_json(
        self,
        system: SystemPrompt,
        user: str,
        schema: type,
        max_tokens: int = 8192,
        model: Optional[str] = None,
    ):
        """Structured JSON -> Pydantic model. Retries once on parse failure."""
        json_instruction = (
            "\n\nYou MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside the JSON."
        )
        full_system = _append_to_system(system, json_instruction)

        text = self._extract_json(self.call(full_system, user, max_tokens, model=model))

        try:
            return schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, Exception) as first_err:
            # Retry once with error feedback
            retry_user = (
                f"Your previous response was not valid JSON.\n"
                f"Error: {first_err}\n\n"
                f"Please return ONLY valid JSON matching the required schema.\n\n"
                f"Original request:\n{user}"
            )
            text2 = self._extract_json(
                self.call(full_system, retry_user, max_tokens, model=model)
            )
            return schema.model_validate(json.loads(text2))

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Strip markdown code fences if the model wrapped its JSON."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # drop opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text


llm = LLM()

HAIKU_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Pass 0: Establish Guiding Principles
# ---------------------------------------------------------------------------

PRINCIPLES_INSTRUCTION = """\
You are a senior AI prompt engineer specialising in healthcare voice agents.
You have been given a canonical library of quality principles (above) and a
specific voice-agent system prompt to evaluate (below).

Your job is to produce an ADAPTIVE BRIEF that focuses downstream passes on
the principles most load-bearing for THIS specific prompt.

Your brief must contain:

1. modality — one of: voice, chat, sms, mixed, unknown.
   Infer from tool names (e.g. send_sms, transfer_call, phone-related tools
   indicate voice), the prompt's language ("phone call", "SMS", "chat"), and
   the overall interaction style. If ambiguous, default to "unknown".

2. domain_signals — short tags describing the domain. For a healthcare
   voice agent you would expect e.g. ["healthcare", "scheduling",
   "appointment_management"]. Include any compliance-relevant tags when
   PHI/HIPAA cues are present.

3. active_principles — the MOST load-bearing subset of canonical principles
   for this prompt. MAXIMUM 12 entries. BE SELECTIVE: choose only principles
   that THIS specific prompt visibly violates or is at clear risk of
   violating. Do NOT list every principle that is theoretically applicable —
   if you find yourself including more than 12, drop the least load-bearing
   until you are at or under the cap.

   For each entry:
   - id: the principle ID (e.g. "STYLE-01", "TOOL-04", "CONTENT-01")
   - reason: one short sentence naming WHY this principle is load-bearing
     for THIS specific input (reference concrete features of the prompt)

4. interaction_contract — a single short paragraph (2–4 sentences) that
   summarises the expected interaction style for this agent given the
   detected modality and domain. For voice + scheduling, this should mention
   brief transactional replies, one targeted follow-up at a time, confirming
   key fields once, never dumping policy text.

5. structure_notes — 1–2 sentences describing the prompt's current structure
   as it relates to cacheability and readability (e.g. is static policy kept
   separate from per-call variables? are there section boundaries? are tools
   listed by name or described in prose?).

IMPORTANT: be CONCRETE. "reason" fields must cite specific aspects of the
input prompt — not generic restatements of the principle.

Return ONLY valid JSON matching this schema:
{
  "modality": "voice",
  "domain_signals": ["healthcare", "scheduling"],
  "active_principles": [
    {"id": "STYLE-01", "reason": "Prompt contains multi-paragraph instructions for a voice call where long replies break the call"},
    ...
  ],
  "interaction_contract": "...",
  "structure_notes": "..."
}"""


def _principles_system_blocks() -> list[dict]:
    """System prompt for Pass 0 — canonical library cached, instructions uncached.

    The canonical library is large and static, so wrapping it in a cache_control
    block lets subsequent calls within a 5-minute window hit the cache.
    """
    return [
        _cached_block(
            "<canonical_principles>\n"
            + CANONICAL_PRINCIPLES_TEXT
            + "\n</canonical_principles>"
        ),
        {"type": "text", "text": PRINCIPLES_INSTRUCTION},
    ]


def _fallback_brief(prompt_text: str, tools_json: Optional[str]) -> PrinciplesBrief:
    """Deterministic brief when the LLM call fails or returns malformed JSON.

    Modality inferred from tool names; active principles are the universally
    applicable subset so downstream passes still receive useful guidance.
    """
    low_text = prompt_text.lower()
    low_tools = (tools_json or "").lower()

    if "send_sms" in low_tools or "transfer_call" in low_tools:
        modality: str = "voice"
    elif "phone call" in low_text or "voice" in low_text:
        modality = "voice"
    elif "sms" in low_tools or "text message" in low_text:
        modality = "sms"
    else:
        modality = "unknown"

    signals = ["healthcare"] if "patient" in low_text or "appointment" in low_text else []
    if "schedule" in low_text or "appointment" in low_text:
        signals.append("scheduling")

    defaults = [
        ("STRUCT-01", "Static/variable separation is a general cacheability concern"),
        ("ROLE-01", "Scope and out-of-scope must be explicit for any agent"),
        ("TOOL-01", "Check for blanket 'always use' tool-use wording"),
        ("TOOL-04", "Parameter formats in prose must match tool schemas"),
        ("TOOL-06", "Tool failure handling is commonly missing"),
        ("ELIG-01", "Eligibility pre-checks before slot offers in healthcare"),
        ("STYLE-01", "Modality-appropriate length for voice replies"),
        ("GUARD-01", "One targeted follow-up on ambiguity"),
        ("CONTENT-01", "Notifications must carry the 5-Ws"),
        ("SAFE-01", "Atomicity for reschedule-style multi-step operations"),
    ]
    active = [ActivePrinciple(id=pid, reason=reason) for pid, reason in defaults]

    return PrinciplesBrief(
        modality=modality,  # type: ignore[arg-type]
        domain_signals=signals,
        active_principles=active,
        interaction_contract=(
            "Voice-modality healthcare agent. Keep replies brief and "
            "transactional. Ask one targeted follow-up at a time. Confirm "
            "the 5 key fields (provider, location, date, time, visit type) "
            "once after a successful booking. Never read out full policy "
            "tables."
        ),
        structure_notes=(
            "Principles brief generated by deterministic fallback — LLM "
            "call failed. Downstream passes evaluate against the default "
            "active-principles set."
        ),
    )


MAX_ACTIVE_PRINCIPLES = 12


def establish_principles(
    prompt_text: str, tools_json: Optional[str] = None
) -> PrinciplesBrief:
    """Pass 0 — produce an adaptive quality lens for this specific prompt.

    Uses Haiku for speed + cost. Falls back to a deterministic brief if the
    LLM call or JSON parse fails, so the pipeline never crashes on Pass 0.
    """
    user_content = f"<prompt>\n{prompt_text}\n</prompt>"
    if tools_json:
        user_content += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"

    try:
        brief = llm.call_json(
            _principles_system_blocks(),
            user_content,
            PrinciplesBrief,
            max_tokens=4096,
            model=HAIKU_MODEL,
        )
    except Exception:
        return _fallback_brief(prompt_text, tools_json)

    # Safety net: enforce the cap even if the LLM overshoots.
    if len(brief.active_principles) > MAX_ACTIVE_PRINCIPLES:
        brief.active_principles = brief.active_principles[:MAX_ACTIVE_PRINCIPLES]
    return brief


def _format_brief_for_passes(brief: Optional[PrinciplesBrief]) -> str:
    """Serialise a brief into the XML block injected into downstream user content."""
    if brief is None:
        return ""
    active = "\n".join(
        f"- {p.id}: {p.reason}" for p in brief.active_principles
    )
    return (
        "<principles_brief>\n"
        f"modality: {brief.modality}\n"
        f"domain_signals: {', '.join(brief.domain_signals) or '(none)'}\n"
        f"interaction_contract: {brief.interaction_contract}\n"
        f"structure_notes: {brief.structure_notes}\n"
        "active_principles:\n"
        f"{active}\n"
        "</principles_brief>"
    )


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------

DETECTION_PROMPT = """\
You are an expert AI prompt engineer specializing in voice agent quality assurance.
Analyze the voice agent system prompt for quality issues across three dimensions:

PATIENT EXPERIENCE — issues causing robotic tone, inadequate empathy, unnatural speech,
poor de-escalation, missing emotional handling for distressed/angry/confused callers,
insufficient personality definition, phone-inappropriate verbosity.

WORKFLOW ADHERENCE — issues causing incorrect tool behavior: parameter format mismatches
between different tools, missing error handling, dangerous operation ordering (e.g. cancel
before rebook with no rollback), business rule contradictions, missing guard conditions
before irreversible actions, ambiguous decision trees, missing workflow paths, instructions
referencing capabilities that don't exist in the tool definitions.

PRINCIPLES — violations of the active canonical principles listed in the
<principles_brief> block that accompanies the input. Each active principle has an ID
(e.g. STYLE-01, TOOL-04, CONTENT-01, SAFE-01). Search the prompt for the violation
signature of each active principle and raise one issue per concrete violation. Assign
issue IDs in the form "PRIN-<PRINCIPLE-ID>" (e.g. "PRIN-STYLE-01", "PRIN-CONTENT-01")
and set dimension to "principles". Only raise a principles issue when the prompt
actually violates the principle — do not raise one just because the principle is listed.

IMPORTANT: You must also analyze the tool definitions (parameters, types, formats) and
cross-reference them against the prompt instructions. Look for format mismatches between
tools, missing parameters in instructions, and instructions that reference tools or
capabilities that don't exist.

For each issue:
- Quote the EXACT text from the prompt as evidence (copy-paste verbatim)
- Rate severity: critical (breaks behavior silently), high (user-facing problem), \
medium (quality degradation), low (minor improvement)
- Assign an ID: WA-XX for workflow adherence, PE-XX for patient experience, \
PRIN-<ID> for principle violations
- Only report genuine issues that would affect production calls, not stylistic preferences

Return JSON matching this schema:
{
  "issues": [
    {
      "id": "WA-01",
      "dimension": "workflow_adherence",
      "severity": "critical",
      "title": "Short descriptive title",
      "description": "What the problem is and why it matters in production",
      "evidence": "Verbatim quote from the prompt proving the issue exists",
      "location_hint": "Which section of the prompt"
    }
  ],
  "analysis_notes": "Brief summary of analysis approach and confidence level"
}"""

REFLECTION_PROMPT = """\
You are reviewing a list of quality issues found in a voice agent prompt.
Your job is to critique this list rigorously.

REMOVE any issues that are:
- False positives (the prompt actually handles it correctly elsewhere — check carefully)
- Stylistic preferences rather than real quality problems
- Duplicates of other issues in the list
- Too vague or speculative to be actionable
- Principles issues raised against a principle that the prompt does NOT actually violate

ADD any obvious high-severity issues that were missed, especially:
- Tool parameter format mismatches between different tools
- Missing instructions for tool failure/error scenarios
- Dangerous operation ordering (irreversible actions without safety checks)
- Business rules stated but unenforceable with available tools
- Missing workflow paths for common caller scenarios
- Violations of active principles in the <principles_brief> not already captured
  (especially: modality-inappropriate verbosity, missing SMS 5-Ws, non-atomic multi-step
  side effects, blanket "always use tool X" wording, static/variable intermixing)

For each issue you keep or add, verify the evidence quote is accurate.

Return the refined JSON with the same schema:
{
  "issues": [...],
  "analysis_notes": "What you changed and why"
}"""

ANALYSIS_PROMPT = """\
You are an expert AI prompt engineer proposing fixes for identified quality issues \
in a voice agent prompt.

For each issue, produce a fix with these components:

1. DIMENSION (dimension): Copy the issue's dimension verbatim — one of \
"workflow_adherence", "patient_experience", or "principles". This routes downstream \
verification (principles-issues skip the behavioral probe).

2. ANCHOR TEXT (anchor_text): Find a short (20-40 character), UNIQUE substring in the \
prompt that is AT or NEAR where the fix should go. This is used to locate the edit site. \
CRITICAL: Verify the anchor appears EXACTLY ONCE in the prompt. If a substring appears \
multiple times, choose a longer or more specific one.

3. ANCHOR CONTEXT (anchor_context): Which section of the prompt this is in (for readability).

4. FIX TYPE (fix_type):
   - "replace": Replace the paragraph/sentence containing the anchor with new_content.
   - "insert_after": Insert new_content as a new paragraph AFTER the paragraph containing the anchor.

5. NEW CONTENT (new_content): The replacement text or new text to add. Keep it minimal \
and consistent with the prompt's existing style. For "replace" fixes, include the FULL \
replacement for the paragraph/block being changed. For "insert_after" fixes, include \
only the new text to add. Your fix must respect the active principles listed in the \
<principles_brief> block — e.g. for a voice modality, keep added text brief and \
transactional; for SMS content, include the 5-Ws; for multi-step side effects, specify \
rollback.

6. ASSERTION (assertion): A plain English statement that can be verified by reading the \
fixed prompt. This is used to confirm the fix was applied correctly.
   - GOOD: "The prompt instructs the agent to format start_date as DD-MM-YYYY when \
calling get_available_slots."
   - BAD: "The date format issue is fixed." (too vague to verify)

7. BEHAVIORAL PROBE (behavioral_probe): A mid-workflow scenario that tests whether the \
fix changes agent behavior at the EXACT decision point where the bug lives.

   The probe MUST:
   - Start PAST identity verification, greeting, and any early workflow steps
   - Place the agent at the specific decision point where this issue manifests
   - Include all necessary prior context as given facts
   - Ask the agent to describe what it does next, including specific tool call parameters

   BAD probe: "I'd like to book an appointment." (too early — agent responds with \
greeting/verification, identical for both original and fixed prompts)

   GOOD probe: "You have verified the patient (Jane Doe, DOB 1985-03-15, patient_id \
P-1234). She wants to cancel her appointment with Dr. Chen at 3:00 PM today. The \
current time is 1:00 PM (within 24 hours). You are about to call cancel_appointment. \
What parameters do you include in the tool call, and what do you say to the patient?"

   For PATIENT EXPERIENCE issues, the probe should place the agent in a conversational \
moment where tone/empathy matters:
   GOOD probe: "The patient has just said: 'I'm really frustrated, I've been trying to \
get an appointment for weeks and nobody has helped me.' You need to respond. What do \
you say?"

   For PRINCIPLES issues, a probe is still required in the schema but it is optional \
for verification — the pipeline verifies principles-issues via the structural assertion \
alone. If no mid-workflow scenario exercises the principle (e.g. static cacheability), \
write a one-line "N/A — verified structurally" string.

Return JSON matching this schema:
{
  "proposals": [
    {
      "issue_id": "WA-01",
      "dimension": "workflow_adherence",
      "root_cause": "Why this issue exists",
      "impact_if_unfixed": "What goes wrong in production",
      "fix_type": "replace",
      "fix_description": "What the fix does",
      "anchor_text": "20-40 char unique substring",
      "anchor_context": "Section name",
      "new_content": "The replacement or new text",
      "assertion": "Verifiable claim about the fixed prompt",
      "behavioral_probe": "Mid-workflow scenario that tests the fix"
    }
  ]
}"""

PROBE_PROMPT = """\
You are an agent following the system prompt below. You are in the MIDDLE of an \
ongoing interaction — the greeting, identity verification, and earlier workflow \
steps have already been completed. Use the modality stated in the \
<principles_brief> (if present) to shape your reply style: for voice, keep SPEECH \
brief and transactional; for chat, short structured replies are fine; for SMS, \
ultra-terse with the 5-Ws where applicable.

The scenario below describes what has happened so far and where you are in the workflow.

Given this state, describe what you do next. Be SPECIFIC about:

SPEECH: [Exactly what you say to the caller — full response, not a summary]
TOOL_CALLS: [List each tool call you would make, with exact function name and ALL \
parameters as JSON. If no tool call is needed, write "none".]
CONDITIONS_CHECKED: [List every condition, rule, or constraint you checked before \
deciding on your action. Include dates compared, limits verified, provider restrictions \
checked, etc. If none, write "none".]

IMPORTANT: You MUST describe actual tool calls with specific parameter values. Do NOT \
skip tool calls or say "I would call the tool" — list the exact parameters you would pass.
If the system prompt instructs you to set a specific parameter (like late_cancel, \
cancelled_by, message_type, etc.), you MUST include it."""

JUDGE_PROMPT = """\
You are an objective, skeptical evaluator comparing two voice agent behavioral \
descriptions at the same decision point in a workflow.

An improvement ONLY counts if:
1. The specific problem behavior actually changed (not just different wording of the \
same action)
2. The tool call parameters, operation ordering, or conditions checked are different \
in a way that addresses the issue
3. No new problems were introduced by the fix
4. The response is still natural and appropriate for a phone call

Score on a 1-10 scale:
- 1-3: No meaningful improvement — same tool parameters, same operation order, same \
conditions (or lack thereof)
- 4-6: Partial improvement — some behavioral change but incomplete or introduces \
minor concerns
- 7-9: Clear improvement — the specific issue is resolved with correct tool \
parameters / ordering / conditions
- 10: Complete resolution — issue fully fixed, all parameters correct, no concerns

FOCUS ON CONCRETE DIFFERENCES:
- Compare TOOL_CALLS parameters between original and fixed — did the right parameters \
get added/changed?
- Compare CONDITIONS_CHECKED — did the agent check something it previously skipped?
- Compare operation ordering — did the sequence of actions change as expected?
- For patient experience issues: compare SPEECH — did tone, empathy, or escalation \
path change?

Be skeptical. Default to lower scores unless the improvement is clearly demonstrated \
in the structured output.

Return JSON:
{
  "improvement_detected": true,
  "improvement_score": 7,
  "explanation": "Quote the specific tool parameters or conditions that changed",
  "remaining_concerns": "Any remaining issues, or null if fully resolved"
}"""

ASSERTION_CHECK_PROMPT = """\
You are checking whether a specific assertion about a voice agent prompt is satisfied.

Read the prompt section below and determine whether the assertion is true.

ASSERTION: {assertion}

Be strict: the assertion must be clearly and specifically satisfied by text in the \
prompt, not merely implied or partially addressed.

Return JSON:
{{
  "passed": true,
  "explanation": "Quote the specific text that satisfies the assertion"
}}"""

LLM_FIX_PROMPT = """\
You are a precise text editor. Apply the described fix to the prompt section below.

Fix description: {fix_description}
New content to incorporate: {new_content}

Return ONLY the modified section text. Do not add explanations or markup. \
Keep all surrounding text unchanged — only modify what the fix requires."""


# ---------------------------------------------------------------------------
# Pass 1: Detection + Reflection
# ---------------------------------------------------------------------------

def _detection_system() -> list[dict]:
    """Cache the canonical principles + static detection instructions together."""
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
    brief_block = _format_brief_for_passes(brief)

    user_content = f"<prompt>\n{prompt_text}\n</prompt>"
    if tools_json:
        user_content += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"
    if brief_block:
        user_content += f"\n\n{brief_block}"

    # Detection pass
    raw = llm.call_json(
        _detection_system(), user_content, DetectionResult, max_tokens=16000
    )

    # Reflection pass — critique and refine
    reflection_input = (
        f"<detected_issues>\n{raw.model_dump_json(indent=2)}\n</detected_issues>"
        f"\n\n<original_prompt>\n{prompt_text}\n</original_prompt>"
    )
    if tools_json:
        reflection_input += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"
    if brief_block:
        reflection_input += f"\n\n{brief_block}"

    refined = llm.call_json(
        _reflection_system(), reflection_input, DetectionResult, max_tokens=16000
    )
    return refined


# ---------------------------------------------------------------------------
# Pass 2: Analysis
# ---------------------------------------------------------------------------

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
) -> AnalysisResult:
    """Propose anchor-based fixes with assertions and behavioral probes."""
    issues_json = json.dumps([i.model_dump() for i in issues], indent=2)
    user_content = (
        f"<prompt>\n{prompt_text}\n</prompt>"
        f"\n\n<issues>\n{issues_json}\n</issues>"
    )
    brief_block = _format_brief_for_passes(brief)
    if brief_block:
        user_content += f"\n\n{brief_block}"
    result = llm.call_json(
        _analysis_system(), user_content, AnalysisResult, max_tokens=16000
    )

    # Post-fill dimension from the source Issue so verify-routing is robust
    # even when the LLM omits the field in its proposal.
    issue_dimension = {i.id: i.dimension for i in issues}
    for proposal in result.proposals:
        if proposal.dimension is None:
            proposal.dimension = issue_dimension.get(proposal.issue_id)
    return result


# ---------------------------------------------------------------------------
# Pass 3: Fix Engine (anchor-based)
# ---------------------------------------------------------------------------

def _find_block_boundaries(text: str, pos: int) -> tuple[int, int]:
    """Find the paragraph/block boundaries around a position.

    A block is delimited by double-newlines (\\n\\n). If none exist nearby,
    fall back to single-newline boundaries.
    """
    # Try double-newline boundaries first
    block_start = text.rfind("\n\n", 0, pos)
    block_start = block_start + 2 if block_start != -1 else 0

    block_end = text.find("\n\n", pos)
    block_end = block_end if block_end != -1 else len(text)

    # If the block is unreasonably large (>2000 chars), try single newlines
    if block_end - block_start > 2000:
        single_start = text.rfind("\n", 0, pos)
        single_start = single_start + 1 if single_start != -1 else 0
        single_end = text.find("\n", pos)
        single_end = single_end if single_end != -1 else len(text)
        if single_end - single_start < block_end - block_start:
            block_start, block_end = single_start, single_end

    return block_start, block_end


def _fuzzy_find(text: str, anchor: str, threshold: float = 0.75) -> int:
    """Find approximate match for anchor in text. Returns position or -1."""
    best_ratio = 0.0
    best_pos = -1
    anchor_len = len(anchor)

    # Slide a window across the text
    for i in range(len(text) - anchor_len + 1):
        candidate = text[i:i + anchor_len]
        ratio = difflib.SequenceMatcher(None, anchor, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = i

    return best_pos if best_ratio >= threshold else -1


def _apply_single_fix(text: str, proposal: FixProposal) -> tuple[str, str]:
    """Apply a single fix using anchor-based location.

    Returns (modified_text, method) where method is one of:
    exact_anchor, fuzzy_anchor, or failed.
    """
    anchor = proposal.anchor_text

    # Strategy 1: exact anchor match
    pos = text.find(anchor)
    if pos != -1:
        # Verify uniqueness
        if text.find(anchor, pos + 1) != -1:
            # Anchor appears multiple times — try to disambiguate by finding
            # the one closest to the anchor_context section
            pass  # Fall through to apply at first occurrence (usually correct)

        block_start, block_end = _find_block_boundaries(text, pos)

        if proposal.fix_type == "replace":
            text = text[:block_start] + proposal.new_content + text[block_end:]
        else:  # insert_after
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]

        return text, "exact_anchor"

    # Strategy 2: fuzzy anchor match
    pos = _fuzzy_find(text, anchor)
    if pos != -1:
        block_start, block_end = _find_block_boundaries(text, pos)

        if proposal.fix_type == "replace":
            text = text[:block_start] + proposal.new_content + text[block_end:]
        else:  # insert_after
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]

        return text, "fuzzy_anchor"

    return text, "failed"


def _llm_assisted_fix(text: str, proposal: FixProposal) -> Optional[str]:
    """Fallback: use LLM to apply a fix on a LOCAL section of the prompt."""
    # Find the best approximate location and extract ~1500 chars around it
    pos = _fuzzy_find(text, proposal.anchor_text, threshold=0.5)
    if pos == -1:
        # Last resort: search for keywords from anchor_context
        pos = len(text) // 2  # middle of text

    context_start = max(0, pos - 750)
    context_end = min(len(text), pos + 750)
    section = text[context_start:context_end]

    system = LLM_FIX_PROMPT.format(
        fix_description=proposal.fix_description,
        new_content=proposal.new_content,
    )
    user = f"Prompt section to modify:\n\n{section}"

    result = llm.call(system, user, max_tokens=4096)

    if result and len(result) > len(section) * 0.3:
        # Replace just the section in the full text
        return text[:context_start] + result + text[context_end:]
    return None


def apply_fixes(
    original_json: dict,
    prompt_text: str,
    proposals: list[FixProposal],
    selected_ids: list[str],
) -> tuple[dict, str, list[str], list[FixValidation]]:
    """Apply selected fixes using anchor-based location.

    Returns (fixed_json, fixed_text, applied_ids, validations).
    """
    selected = [p for p in proposals if p.issue_id in selected_ids]
    text = prompt_text
    applied: list[str] = []
    validations: list[FixValidation] = []

    for proposal in selected:
        new_text, method = _apply_single_fix(text, proposal)

        if method == "failed":
            # Try LLM-assisted fallback on local section
            fallback_text = _llm_assisted_fix(text, proposal)
            if fallback_text:
                text = fallback_text
                method = "llm_fallback"
                applied.append(proposal.issue_id)
            else:
                validations.append(FixValidation(
                    issue_id=proposal.issue_id,
                    applied=False,
                    method="failed",
                    assertion_passed=False,
                    explanation="Could not locate anchor text in prompt.",
                ))
                continue
        else:
            text = new_text
            applied.append(proposal.issue_id)

        # Validate: check assertion against the fixed text
        validation = _check_assertion(text, proposal, method)
        validations.append(validation)

    # Update the prompt field in the JSON
    fixed_json = dict(original_json)
    prompt_key = None
    for key in KNOWN_KEYS:
        if key in fixed_json:
            prompt_key = key
            break
    if not prompt_key:
        longest_len = 0
        for key, value in fixed_json.items():
            if isinstance(value, str) and len(value) > longest_len:
                prompt_key = key
                longest_len = len(value)
    if prompt_key:
        fixed_json[prompt_key] = text

    return fixed_json, text, applied, validations


# ---------------------------------------------------------------------------
# Pass 3b: Fix Validation
# ---------------------------------------------------------------------------

def _check_assertion(
    fixed_text: str, proposal: FixProposal, method: str
) -> FixValidation:
    """Check whether a fix's assertion holds in the fixed prompt text."""
    assertion = proposal.assertion

    # First: check if new_content is present in the fixed text (simple containment)
    # This handles the majority of cases where the fix adds specific text
    key_phrases = _extract_key_phrases(assertion)
    simple_pass = any(phrase.lower() in fixed_text.lower() for phrase in key_phrases)

    if simple_pass:
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=True,
            explanation=f"Key content found in fixed prompt (matched: {key_phrases[0][:50]}...).",
        )

    # Fallback: check if new_content itself is present
    if proposal.new_content[:80] in fixed_text:
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=True,
            explanation="New content successfully inserted into prompt.",
        )

    # If simple checks don't pass, use LLM on the relevant section
    pos = fixed_text.find(proposal.anchor_text)
    if pos == -1:
        pos = _fuzzy_find(fixed_text, proposal.anchor_text, threshold=0.5)
    if pos == -1:
        pos = len(fixed_text) // 2

    section_start = max(0, pos - 500)
    section_end = min(len(fixed_text), pos + 500)
    section = fixed_text[section_start:section_end]

    system = ASSERTION_CHECK_PROMPT.format(assertion=assertion)
    user = f"<prompt_section>\n{section}\n</prompt_section>"

    try:
        result = llm.call_json(system, user, AssertionCheck, max_tokens=1024)
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=result.passed,
            explanation=result.explanation,
        )
    except Exception:
        # If LLM check fails, be optimistic if the fix was applied
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=True,
            explanation="Assertion check inconclusive; fix was applied.",
        )


def _extract_key_phrases(assertion: str) -> list[str]:
    """Extract testable key phrases from an assertion string.

    Looks for quoted strings, tool parameter names, and specific format patterns.
    """
    phrases = []

    # Extract quoted strings
    quoted = re.findall(r'"([^"]+)"', assertion)
    phrases.extend(quoted)
    quoted_single = re.findall(r"'([^']+)'", assertion)
    phrases.extend(quoted_single)

    # Extract tool-like terms (e.g., "late_cancel", "DD-MM-YYYY", "cancelled_by")
    tool_terms = re.findall(r'\b[a-z_]+(?:_[a-z_]+)+\b', assertion)
    phrases.extend(tool_terms)

    # Extract format patterns
    formats = re.findall(r'[A-Z]{2,4}-[A-Z]{2,4}-[A-Z]{2,4}', assertion)
    phrases.extend(formats)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for p in phrases:
        if p.lower() not in seen and len(p) >= 3:
            seen.add(p.lower())
            unique.append(p)

    return unique if unique else [assertion[:60]]


# ---------------------------------------------------------------------------
# Pass 4: Verification (structural + behavioral probe)
# ---------------------------------------------------------------------------

def verify(
    issue_id: str,
    proposal: FixProposal,
    original_prompt: str,
    fixed_prompt: str,
    brief: Optional[PrinciplesBrief] = None,
) -> VerificationResult:
    """Verify a fix via structural assertion check + behavioral probe comparison.

    For principles-dimension issues, skip the probe + judge path and emit a
    verdict from the structural assertion alone — these issues rarely have a
    clean mid-workflow decision point and produce noisy judge scores.
    """

    # -- Prong A: Structural check --
    key_phrases = _extract_key_phrases(proposal.assertion)
    structural_pass = any(
        phrase.lower() in fixed_prompt.lower() for phrase in key_phrases
    )
    if not structural_pass and proposal.new_content[:80] in fixed_prompt:
        structural_pass = True

    # -- Principles issues: assertion-only verification --
    if proposal.dimension == "principles":
        return VerificationResult(
            issue_id=issue_id,
            structural_pass=structural_pass,
            behavioral_pass=structural_pass,
            improvement_score=8 if structural_pass else 2,
            explanation=(
                "Principles issue verified structurally via assertion. "
                "No behavioral probe applicable for this principle."
            ),
            original_probe_response="N/A — principles issue, probe skipped.",
            fixed_probe_response="N/A — principles issue, probe skipped.",
            remaining_concerns=None,
        )

    # -- Prong B: Behavioral probe (non-principles issues) --
    probe_context = proposal.behavioral_probe
    brief_block = _format_brief_for_passes(brief)

    probe_user = (
        f"SCENARIO (mid-workflow state):\n{probe_context}\n\n"
        f"What do you do next? Respond with SPEECH, TOOL_CALLS, and CONDITIONS_CHECKED."
    )

    def _probe_system(system_prompt: str) -> str:
        parts = [PROBE_PROMPT]
        if brief_block:
            parts.append(brief_block)
        parts.append(f"<system_prompt>\n{system_prompt}\n</system_prompt>")
        return "\n\n".join(parts)

    # Probe with original prompt
    orig_response = llm.call(_probe_system(original_prompt), probe_user)
    # Probe with fixed prompt
    fixed_response = llm.call(_probe_system(fixed_prompt), probe_user)

    # -- Judge the difference (judge receives NO brief — stays channel-agnostic) --
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
    verdict_data = llm.call_json(JUDGE_PROMPT, judge_input, JudgeRaw)

    return VerificationResult(
        issue_id=issue_id,
        structural_pass=structural_pass,
        behavioral_pass=verdict_data.improvement_detected,
        improvement_score=verdict_data.improvement_score,
        explanation=verdict_data.explanation,
        original_probe_response=orig_response,
        fixed_probe_response=fixed_response,
        remaining_concerns=verdict_data.remaining_concerns,
    )
