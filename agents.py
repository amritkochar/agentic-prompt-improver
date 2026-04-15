"""All LLM agent passes: detect, reflect, analyze, fix, validate, verify."""

from __future__ import annotations

import difflib
import json
import logging
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
from schema_registry import build_registry_from_json_text

logger = logging.getLogger(__name__)


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
    def __init__(self, model: str = "claude-haiku-4-5"):
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
        """Plain text response. `system` may be a str or a list of content blocks.

        Uses streaming for large max_tokens to avoid the SDK's 10-minute
        non-streaming timeout guard (triggers around ~21k output tokens).
        """
        model_id = model or self.model
        kwargs = {
            "model": model_id,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if max_tokens > 16000:
            with self.client.messages.stream(**kwargs) as stream:
                for _ in stream.text_stream:
                    pass
                final = stream.get_final_message()
            self._record(model_id, final.usage)
            return final.content[0].text

        response = self.client.messages.create(**kwargs)
        self._record(model_id, response.usage)
        return response.content[0].text

    def call_json(
        self,
        system: SystemPrompt,
        user: str,
        schema: type,
        max_tokens: int = 16000,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        """Structured JSON -> Pydantic model. Retries on parse failure.

        Extraction is lenient: strips markdown fences, then falls back to
        first-`{` / last-`}` carving so a stray preamble does not fail the
        whole call. Retries feed the parse error back to the model.
        """
        json_instruction = (
            "\n\nYou MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside the JSON."
        )
        full_system = _append_to_system(system, json_instruction)

        last_err: Optional[Exception] = None
        current_user = user

        for attempt in range(max_retries + 1):
            raw = self.call(full_system, current_user, max_tokens, model=model)
            text = self._extract_json(raw)
            try:
                return schema.model_validate(json.loads(text))
            except Exception as err:
                last_err = err
                logger.warning(
                    "call_json parse failure (attempt %d/%d) for %s: %s",
                    attempt + 1, max_retries + 1, schema.__name__, err,
                )
                current_user = (
                    f"Your previous response could not be parsed as valid JSON matching the "
                    f"{schema.__name__} schema.\nError: {err}\n\n"
                    f"Return ONLY valid JSON. No markdown, no prose.\n\n"
                    f"Original request:\n{user}"
                )

        assert last_err is not None
        raise last_err

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Best-effort isolation of the JSON blob in an LLM response.

        Handles: code fences (```json ... ```), prose preambles/postambles,
        and bare JSON. Returns the raw string unchanged if no obvious carving
        improves it, so json.loads still surfaces the original error.
        """
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # drop opening ```json (or ```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # If the model wrapped its JSON in prose, carve on outermost braces.
        if not (text.startswith("{") and text.endswith("}")):
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last > first:
                text = text[first:last + 1]
        return text


llm = LLM()

HAIKU_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Pass 0: Establish Guiding Principles
# ---------------------------------------------------------------------------

PRINCIPLES_INSTRUCTION = """\
You are a senior AI prompt engineer. You have been given a canonical library
of quality principles (above) and a specific conversational-agent system
prompt to evaluate (below).

Your job is to produce an ADAPTIVE BRIEF that focuses downstream passes on
the principles most load-bearing for THIS specific prompt. The tool was
originally tuned for healthcare voice agents, so be careful to stay
domain-agnostic — infer what this prompt actually is and let that drive your
selections, rather than assuming a booking workflow.

Your brief must contain:

1. modality — one of: voice, chat, sms, mixed, unknown.
   Infer from tool names (e.g. send_sms, transfer_call, phone-related tools
   indicate voice), the prompt's language ("phone call", "SMS", "chat"), and
   the overall interaction style. If ambiguous, default to "unknown".

2. domain — a single short slug identifying the primary domain. Examples:
   "healthcare", "fintech", "insurance", "customer_support",
   "telecom", "ecommerce", "travel", "scheduling", "unknown". Pick the single
   closest tag based on the prompt's vocabulary, tool set, and workflows.

3. domain_signals — 2-6 short tags adding nuance ("scheduling",
   "compliance", "phi", "fraud_check", "subscription_mgmt", etc.).

4. active_principles — the MOST load-bearing subset of canonical principles
   for this prompt. MAXIMUM 12 entries. BE SELECTIVE: choose only principles
   that THIS specific prompt visibly violates or is at clear risk of
   violating. Do NOT list every principle that is theoretically applicable —
   if you find yourself including more than 12, drop the least load-bearing
   until you are at or under the cap.

   For each entry:
   - id: the principle ID (e.g. "STYLE-01", "TOOL-04", "CONTENT-01")
   - reason: one short sentence naming WHY this principle is load-bearing
     for THIS specific input (reference concrete features of the prompt)

5. interaction_contract — a single short paragraph (2–4 sentences) that
   summarises the expected interaction style for this agent given the
   detected modality and domain. Tailor wording to the domain (e.g. voice
   scheduling gets "brief transactional replies, one question at a time";
   chat fraud-check gets "confirm intent before destructive actions").

6. structure_notes — 1–2 sentences describing the prompt's current structure
   as it relates to cacheability and readability (e.g. is static policy kept
   separate from per-call variables? are there section boundaries? are tools
   listed by name or described in prose?).

IMPORTANT: be CONCRETE. "reason" fields must cite specific aspects of the
input prompt — not generic restatements of the principle.

Return ONLY valid JSON matching this schema:
{
  "modality": "voice",
  "domain": "healthcare",
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


def _fallback_brief(
    prompt_text: str,
    tools_json: Optional[str],
    reason: str = "LLM call failed",
) -> PrinciplesBrief:
    """Deterministic brief when the LLM call fails or returns malformed JSON.

    Modality and domain inferred heuristically from the prompt + tool names;
    active principles are the universally applicable subset so downstream
    passes still receive useful guidance. Healthcare-specific principles are
    only included when healthcare signals are actually present.
    """
    low_text = prompt_text.lower()
    low_tools = (tools_json or "").lower()

    # Modality
    if "send_sms" in low_tools or "transfer_call" in low_tools:
        modality: str = "voice"
    elif "phone call" in low_text or "voice" in low_text:
        modality = "voice"
    elif "sms" in low_tools or "text message" in low_text:
        modality = "sms"
    elif "chat" in low_text or "message the user" in low_text:
        modality = "chat"
    else:
        modality = "unknown"

    # Domain inference (keyword-driven; cheap heuristic for fallback path only)
    domain_keywords = [
        ("healthcare", ("patient", "appointment", "provider", "clinic", "medical")),
        ("fintech", ("transaction", "account balance", "fraud", "card", "refund")),
        ("insurance", ("policy", "claim", "coverage", "deductible")),
        ("ecommerce", ("order", "shipment", "return", "cart")),
        ("telecom", ("subscriber", "plan", "outage")),
        ("travel", ("booking", "flight", "itinerary", "reservation")),
    ]
    domain = "unknown"
    signals: list[str] = []
    for tag, kws in domain_keywords:
        if any(kw in low_text for kw in kws):
            if domain == "unknown":
                domain = tag
            signals.append(tag)
    if "schedule" in low_text or "appointment" in low_text:
        signals.append("scheduling")

    defaults = [
        ("STRUCT-01", "Static/variable separation is a general cacheability concern"),
        ("ROLE-01", "Scope and out-of-scope must be explicit for any agent"),
        ("TOOL-01", "Check for blanket 'always use' tool-use wording"),
        ("TOOL-04", "Parameter formats in prose must match tool schemas"),
        ("TOOL-06", "Tool failure handling is commonly missing"),
        ("STYLE-01", "Modality-appropriate response length"),
        ("GUARD-01", "One targeted follow-up on ambiguity"),
        ("SAFE-01", "Atomicity for multi-step side-effect operations"),
    ]
    if domain == "healthcare":
        defaults.extend([
            ("ELIG-01", "Eligibility pre-checks before slot offers"),
            ("CONTENT-01", "Notifications must carry the 5-Ws"),
        ])
    active = [ActivePrinciple(id=pid, reason=r) for pid, r in defaults]

    return PrinciplesBrief(
        modality=modality,  # type: ignore[arg-type]
        domain=domain,
        domain_signals=list(dict.fromkeys(signals)),
        active_principles=active,
        interaction_contract=(
            f"{modality.title()}-modality {domain} agent. Keep replies concise "
            "and on-task, ask one follow-up at a time when information is "
            "missing, confirm key fields before irreversible actions, and "
            "avoid dumping policy text to the caller."
        ),
        structure_notes=(
            f"Principles brief generated by deterministic fallback "
            f"(reason: {reason}). Downstream passes evaluate against the "
            "default active-principles set."
        ),
    )


MAX_ACTIVE_PRINCIPLES = 12

# Judge score (1-10) that marks a fix as a clear behavioral improvement.
# Scores 4-6 are "partial" and do NOT count as behavioral_pass.
BEHAVIORAL_PASS_THRESHOLD = 7


def establish_principles(
    prompt_text: str, tools_json: Optional[str] = None
) -> PrinciplesBrief:
    """Pass 0 — produce an adaptive quality lens for this specific prompt.

    Uses Haiku for speed + cost. Falls back to a deterministic brief if the
    LLM call or JSON parse fails, so the pipeline never crashes on Pass 0.
    Attaches a deterministic tool_schema_registry so downstream passes can
    cross-reference tool constraints systematically.
    """
    registry = build_registry_from_json_text(tools_json)

    user_content = f"<prompt>\n{prompt_text}\n</prompt>"
    if tools_json:
        user_content += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"
    if registry:
        user_content += f"\n\n{registry}"

    try:
        brief = llm.call_json(
            _principles_system_blocks(),
            user_content,
            PrinciplesBrief,
            max_tokens=4096,
            model=HAIKU_MODEL,
        )
    except Exception as err:
        logger.warning("Pass 0 LLM call failed, using deterministic fallback: %s", err)
        brief = _fallback_brief(prompt_text, tools_json, reason=str(err)[:80])

    # Safety net: enforce the cap even if the LLM overshoots.
    if len(brief.active_principles) > MAX_ACTIVE_PRINCIPLES:
        brief.active_principles = brief.active_principles[:MAX_ACTIVE_PRINCIPLES]

    # Attach the deterministic registry regardless of LLM success.
    brief.tool_schema_registry = registry
    return brief


def _format_brief_for_passes(brief: Optional[PrinciplesBrief]) -> str:
    """Serialise a brief into the XML block injected into downstream user content."""
    if brief is None:
        return ""
    active = "\n".join(
        f"- {p.id}: {p.reason}" for p in brief.active_principles
    )
    block = (
        "<principles_brief>\n"
        f"modality: {brief.modality}\n"
        f"domain: {brief.domain}\n"
        f"domain_signals: {', '.join(brief.domain_signals) or '(none)'}\n"
        f"interaction_contract: {brief.interaction_contract}\n"
        f"structure_notes: {brief.structure_notes}\n"
        "active_principles:\n"
        f"{active}\n"
        "</principles_brief>"
    )
    if brief.tool_schema_registry:
        block += "\n\n" + brief.tool_schema_registry
    return block


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------

DETECTION_PROMPT = """\
You are an expert AI prompt engineer performing quality assurance on a
conversational agent system prompt. The agent may be voice, chat, or SMS; may
operate in any domain (healthcare, fintech, insurance, customer support,
etc.). Use the <principles_brief> (modality, domain, active_principles) and
the <tool_schema_registry> (if present) to shape your analysis rather than
assuming a healthcare booking workflow.

Analyze the prompt for quality issues across three dimensions:

CALLER EXPERIENCE — issues causing robotic tone, inadequate empathy,
unnatural speech for the given modality, poor de-escalation, missing
emotional handling for upset/confused callers, insufficient personality
definition, modality-inappropriate verbosity. Use dimension
"patient_experience" (legacy label; applies to all caller-experience issues
regardless of domain).

WORKFLOW ADHERENCE — issues causing incorrect tool behavior: parameter
format mismatches between different tools, missing error handling,
dangerous operation ordering (e.g. destructive action before backup/rollback),
business rule contradictions, missing guard conditions before irreversible
actions, ambiguous decision trees, missing workflow paths, instructions
referencing capabilities that don't exist in the tool definitions. Use
dimension "workflow_adherence".

PRINCIPLES — violations of the active canonical principles listed in the
<principles_brief> block. Each active principle has an ID (e.g. STYLE-01,
TOOL-04, CONTENT-01, SAFE-01). Search the prompt for the violation signature
of each active principle and raise one issue per concrete violation. Assign
issue IDs in the form "PRIN-<PRINCIPLE-ID>" and set dimension to
"principles". Only raise a principles issue when the prompt actually violates
the principle — do not raise one just because the principle is listed.

SYSTEMATIC SCHEMA CROSS-REFERENCE (if <tool_schema_registry> is present):
walk each tool's declared params and verify that any prose instructions in
the prompt about that param (format strings, enums, required fields) match
the registry. Mismatches are high-severity TOOL-04-style issues even when
subtle. If the registry is missing, fall back to reading the tool_definitions
JSON directly.

For each issue:
- Quote the EXACT text from the prompt as evidence (copy-paste verbatim)
- Rate severity: critical (breaks behavior silently), high (user-facing problem), \
medium (quality degradation), low (minor improvement)
- Assign an ID: WA-XX for workflow adherence, PE-XX for caller experience, \
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
You are reviewing a list of quality issues found in a conversational agent
prompt. Your job is to critique this list rigorously. The agent may be in
any domain — use the <principles_brief> domain/modality to calibrate what
counts as a real issue versus a stylistic preference.

REMOVE any issues that are:
- False positives (the prompt actually handles it correctly elsewhere — check carefully)
- Stylistic preferences rather than real quality problems
- Duplicates of other issues in the list
- Too vague or speculative to be actionable
- Principles issues raised against a principle that the prompt does NOT actually violate
- Domain-inapplicable (e.g. flagging a missing "eligibility check" on a non-scheduling agent)

ADD any obvious high-severity issues that were missed, especially:
- Tool parameter format/enum mismatches (cross-check against <tool_schema_registry>)
- Missing instructions for tool failure/error scenarios
- Dangerous operation ordering (irreversible actions without safety checks)
- Business rules stated but unenforceable with available tools
- Missing workflow paths for common caller scenarios
- Violations of active principles in the <principles_brief> not already captured

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

   ADVERSARIAL FRAMING (CRITICAL): A capable agent will often do the right thing by \
reading tool schemas or applying general common sense — even when the prompt is \
silent or wrong on the rule. If your probe does not make the WRONG behavior \
tempting, both the original and fixed agent will produce the same correct output \
and the judge will rule the comparison "inconclusive" (a wasted verification). \
Before committing the probe, ask yourself: "If the agent ignored the fixed prompt \
text entirely, could it still arrive at the correct answer from the tool schema or \
obvious context?" If yes, the probe is too easy — add a distractor that baits the \
original prompt's failure mode:
     - For format bugs: supply the data in a DIFFERENT format than required (e.g. \
"current_time returned 2026-04-14; the patient said June 5th 2026" — invites \
copying YYYY-MM-DD when the tool wants DD-MM-YYYY).
     - For ordering bugs: make the wrong-order path look equally natural (e.g. \
"the patient said yes to the new slot" — invites immediate cancel-then-book).
     - For missing-parameter bugs: describe the scenario without naming the \
parameter, so the original prompt's omission is the only thing stopping the agent \
from forgetting it.
     - For workflow-sequence bugs: end the scenario at the precise step BEFORE the \
one that is usually skipped, not after it.

   BAD probe: "I'd like to book an appointment." (too early — identical behavior)
   BAD probe: "What format do you pass for start_date?" (schema tells the agent \
the format; both prompts answer the same.)

   GOOD probe: "You have verified the patient (Jane Doe, DOB 1985-03-15, patient_id \
P-1234). She wants to cancel her appointment with Dr. Chen at 3:00 PM today. The \
current time is 1:00 PM. You are about to call cancel_appointment. What parameters \
do you include, and what do you say to the patient?"
   GOOD probe: "You have verified the patient. She wants to reschedule her physical \
to the June 24th 9 AM slot you just confirmed. She said 'yes, that works.' What is \
the exact sequence of your next tool calls and speech?" (the natural-feeling \
wrong path is cancel → book; the right path is book → cancel)

   For CALLER EXPERIENCE issues (dimension "patient_experience"), the probe \
should place the agent in a conversational moment where tone/empathy matters:
   GOOD probe: "The caller has just said: 'I'm really frustrated, I've been trying to \
resolve this for weeks and nobody has helped me.' You need to respond. What do you say?"

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

Your job is to decide which of four categories best fits the comparison, then assign \
a 1-10 score consistent with that category.

CATEGORIES:
- "improved": The fixed behavior clearly resolves the stated issue — tool parameters, \
ordering, conditions, or speech changed in a way that directly addresses the root \
cause, and the original behavior was actually exhibiting the bug. Score 7-10.

- "inconclusive": The ORIGINAL behavior was already correct for this scenario. Both \
behaviors pass the same tool parameters / check the same conditions / produce the \
same appropriate speech, and both are the *right* thing to do. The probe scenario \
did not actually exercise the failure mode described in the issue. This is NOT a \
fix failure — it's a probe-design failure. Score 1-3 (reflects that no improvement \
can be measured, not that the fix is bad). Use this when you notice the original \
already does the correct thing.

- "unchanged": The fix did NOT meaningfully alter the problematic behavior. Either \
the output is identical AND still wrong, or changes are cosmetic (wording, added \
conditions list entries) without altering tool calls / ordering / enforced \
conditions. The bug is still present. Score 1-4.

- "regressed": The fix introduced a NEW problem — removed a clarification step, \
made an unwarranted assumption, dropped a required parameter, changed tone \
inappropriately. Score 1-3.

Partial improvements — fix addresses part of the issue but misses another part, or \
changes ordering but leaves a sub-rule violated — go under "unchanged" with score \
4-6. Do NOT call partial wins "improved".

FOCUS ON CONCRETE DIFFERENCES between original and fixed:
- TOOL_CALLS parameters — did the right params get added/changed/removed?
- CONDITIONS_CHECKED — did the agent actually check something it previously skipped, \
or was the new entry just restated intent?
- Operation ordering — did the sequence of tool calls change as expected?
- SPEECH — for caller-experience issues, did tone / empathy / escalation path change?

Be skeptical and precise. If the original already produced the correct output, \
choose "inconclusive" — do not reward the fix for matching behavior that was already \
right.

Return JSON:
{
  "verdict": "improved" | "inconclusive" | "unchanged" | "regressed",
  "improvement_score": 1-10,
  "explanation": "Quote the specific tool parameters, conditions, or speech that did or didn't change, and state why the verdict fits.",
  "remaining_concerns": "What still needs fixing (if unchanged/regressed), what a sharper probe should test (if inconclusive), or null (if improved)"
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
        _detection_system(), user_content, DetectionResult, max_tokens=32000
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
        _reflection_system(), reflection_input, DetectionResult, max_tokens=32000
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
    retry_feedback: Optional[str] = None,
) -> AnalysisResult:
    """Propose anchor-based fixes with assertions and behavioral probes.

    When `retry_feedback` is provided, it is embedded in the user message so
    the LLM sees *why* its prior proposals failed (judge verdict, remaining
    concerns, or probe-design issues) and can propose a different fix —
    different anchor, different new_content, or a sharper adversarial probe.
    """
    issues_json = json.dumps([i.model_dump() for i in issues], indent=2)
    user_content = (
        f"<prompt>\n{prompt_text}\n</prompt>"
        f"\n\n<issues>\n{issues_json}\n</issues>"
    )
    brief_block = _format_brief_for_passes(brief)
    if brief_block:
        user_content += f"\n\n{brief_block}"
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


def _fuzzy_find(
    text: str, anchor: str, threshold: float = 0.75
) -> tuple[int, float]:
    """Find the best approximate match for anchor in text.

    Returns (position, ratio). Position is -1 if ratio is below threshold.
    The ratio is always surfaced so the caller can expose confidence to
    the UI.
    """
    best_ratio = 0.0
    best_pos = -1
    anchor_len = len(anchor)
    if anchor_len == 0 or anchor_len > len(text):
        return -1, 0.0

    for i in range(len(text) - anchor_len + 1):
        candidate = text[i:i + anchor_len]
        ratio = difflib.SequenceMatcher(None, anchor, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = i

    if best_ratio < threshold:
        return -1, best_ratio
    return best_pos, best_ratio


def _all_occurrences(text: str, needle: str) -> list[int]:
    """Return every start offset where needle occurs in text."""
    if not needle:
        return []
    out: list[int] = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx == -1:
            return out
        out.append(idx)
        start = idx + 1


def _disambiguate_duplicate_anchor(
    text: str, positions: list[int], context_hint: Optional[str]
) -> Optional[int]:
    """Pick the occurrence whose surrounding block best matches context_hint.

    Returns the chosen position, or None if no clear winner. The context hint
    is the proposal.anchor_context (e.g. "Cancellation section"); we score by
    normalised token overlap with the block text around each occurrence.
    """
    if not context_hint or not context_hint.strip():
        return None

    hint_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", context_hint)
    }
    if not hint_tokens:
        return None

    scores: list[tuple[int, int]] = []  # (score, pos)
    for pos in positions:
        block_start, block_end = _find_block_boundaries(text, pos)
        # Widen a bit so section headings immediately above the paragraph count too.
        window = text[max(0, block_start - 200):min(len(text), block_end + 200)]
        window_tokens = {
            t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", window)
        }
        scores.append((len(hint_tokens & window_tokens), pos))

    scores.sort(reverse=True)
    best_score, best_pos = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1

    # Refuse to pick if the top match isn't meaningfully better than the
    # runner-up — that ambiguity is what silently produces wrong edits.
    if best_score == 0 or best_score == second_score:
        return None
    return best_pos


def _apply_single_fix(
    text: str, proposal: FixProposal
) -> tuple[str, str, Optional[float], str]:
    """Apply a single fix using anchor-based location.

    Returns (modified_text, method, confidence, reason) where:
      - method ∈ {"exact_anchor", "fuzzy_anchor", "failed"}
      - confidence is the fuzzy match ratio when method == "fuzzy_anchor",
        else None
      - reason is a short human-readable explanation (empty on success)
    """
    anchor = proposal.anchor_text

    # Strategy 1: exact anchor match
    positions = _all_occurrences(text, anchor)
    if len(positions) == 1:
        pos = positions[0]
    elif len(positions) > 1:
        chosen = _disambiguate_duplicate_anchor(
            text, positions, proposal.anchor_context
        )
        if chosen is None:
            logger.warning(
                "Ambiguous anchor for %s: %d occurrences, no clear winner from context",
                proposal.issue_id, len(positions),
            )
            return (
                text,
                "failed",
                None,
                (
                    f"Anchor appears {len(positions)} times and anchor_context "
                    "did not pick a clear winner — refusing to guess."
                ),
            )
        pos = chosen
        logger.info(
            "Disambiguated anchor for %s: chose offset %d of %d via context",
            proposal.issue_id, pos, len(positions),
        )
    else:
        pos = -1

    if pos != -1:
        block_start, block_end = _find_block_boundaries(text, pos)
        if proposal.fix_type == "replace":
            text = text[:block_start] + proposal.new_content + text[block_end:]
        else:  # insert_after
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]
        return text, "exact_anchor", None, ""

    # Strategy 2: fuzzy anchor match
    fuzzy_pos, ratio = _fuzzy_find(text, anchor)
    if fuzzy_pos != -1:
        block_start, block_end = _find_block_boundaries(text, fuzzy_pos)
        if proposal.fix_type == "replace":
            text = text[:block_start] + proposal.new_content + text[block_end:]
        else:  # insert_after
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]
        return text, "fuzzy_anchor", ratio, ""

    return text, "failed", None, f"Anchor not found (best fuzzy ratio: {ratio:.2f})."


def _llm_assisted_fix(text: str, proposal: FixProposal) -> Optional[str]:
    """Fallback: use LLM to apply a fix on a LOCAL section of the prompt."""
    # Find the best approximate location and extract ~1500 chars around it
    pos, _ratio = _fuzzy_find(text, proposal.anchor_text, threshold=0.5)
    if pos == -1:
        # Last resort: center the window on the middle of the text.
        pos = len(text) // 2

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
        new_text, method, confidence, failure_reason = _apply_single_fix(text, proposal)

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
                    explanation=failure_reason or "Could not locate anchor text in prompt.",
                    match_confidence=confidence,
                ))
                continue
        else:
            text = new_text
            applied.append(proposal.issue_id)

        # Validate: check assertion against the fixed text
        validation = _check_assertion(text, proposal, method)
        if confidence is not None:
            validation.match_confidence = confidence
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
        pos, _ratio = _fuzzy_find(fixed_text, proposal.anchor_text, threshold=0.5)
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
    except Exception as err:
        # Previously this was optimistic (returned passed=True). That masked
        # real failures. Treat the inability to verify as an assertion failure
        # so the downstream verification gate actually catches it.
        logger.warning(
            "Assertion LLM check failed for %s: %s", proposal.issue_id, err
        )
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=False,
            explanation=f"Assertion check could not complete ({err}).",
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

def _generate_followup(
    probe_context: str,
    agent_reply: str,
    issue_description: str,
    prior_turns: list[tuple[str, str]],
) -> str:
    """Ask a small LLM call to produce the next caller message in a probe trace."""
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
        return llm.call(FOLLOWUP_GENERATOR_PROMPT, user, max_tokens=300).strip()
    except Exception as err:
        logger.warning("Follow-up generator failed: %s", err)
        return "(follow-up could not be generated)"


def _run_probe_trace(
    system_prompt: str,
    initial_user: str,
    num_turns: int,
    issue_id: str,
    proposal: FixProposal,
) -> str:
    """Run a 1..N-turn probe against one system prompt and return a flat trace.

    The caller message on each subsequent turn is generated by a helper LLM
    that sees only the conversation so far + the issue description. This
    mirrors the single-turn path when num_turns == 1, so no behavioural
    change for callers that do not opt in.
    """
    first_reply = llm.call(system_prompt, initial_user)
    if num_turns <= 1:
        return first_reply

    turns: list[tuple[str, str]] = [(initial_user, first_reply)]
    trace_lines = [f"--- Turn 1 ---\nCALLER: {initial_user}\nAGENT: {first_reply}"]

    issue_description = (
        f"{proposal.fix_description} — root cause: {proposal.root_cause}. "
        f"Assertion: {proposal.assertion}"
    )

    for turn_idx in range(2, num_turns + 1):
        followup = _generate_followup(
            proposal.behavioral_probe, first_reply, issue_description, turns
        )
        try:
            reply = llm.call(system_prompt, followup)
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


FOLLOWUP_GENERATOR_PROMPT = """\
You are simulating a caller probing an agent for a known quality issue. You
have already sent one message and received one response. Write the caller's
NEXT message — a short, natural, on-topic follow-up that pressures the same
decision point the original probe was targeting.

The follow-up should:
- Stay in character as the caller
- React to something concrete in the agent's reply (disagreement, clarification, new detail)
- Continue to exercise the issue being tested — do not drift to unrelated topics
- Be 1-3 sentences max

Return ONLY the caller's next message, no preamble."""


def verify(
    issue_id: str,
    proposal: FixProposal,
    original_prompt: str,
    fixed_prompt: str,
    brief: Optional[PrinciplesBrief] = None,
    num_turns: int = 1,
) -> VerificationResult:
    """Verify a fix via structural assertion check + behavioral probe comparison.

    For principles-dimension issues, skip the probe + judge path and emit a
    verdict from the structural assertion alone — these issues rarely have a
    clean mid-workflow decision point and produce noisy judge scores.

    num_turns > 1 triggers multi-turn simulation: the caller's follow-ups are
    generated by a separate LLM call (seeing only the conversation so far),
    then replayed against BOTH original and fixed agents. The judge sees the
    full traces.
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
            verdict_category="improved" if structural_pass else "unchanged",
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

    orig_response = _run_probe_trace(
        _probe_system(original_prompt), initial_user, num_turns, issue_id, proposal
    )
    fixed_response = _run_probe_trace(
        _probe_system(fixed_prompt), initial_user, num_turns, issue_id, proposal
    )

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

    # behavioral_pass = only the "improved" category passes. Score threshold
    # is enforced as a sanity check: a judge labeling something "improved"
    # with score < 7 is inconsistent and gets demoted.
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
