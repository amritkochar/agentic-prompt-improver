"""Prompt templates for every LLM pass.

Pure string constants. Keeping them in one module makes iteration on wording
cheap — edit here, no Python logic to reason about. Each constant names the
pass it belongs to. Curly-brace `{…}` placeholders are filled via `.format()`
at the call site.
"""

from __future__ import annotations


# ── Pass 0: Establish Guiding Principles ─────────────────────────────────────

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


# ── Pass 1: Detection + Reflection ───────────────────────────────────────────

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


# ── Pass 2: Analysis ─────────────────────────────────────────────────────────

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

PRIOR-RUN LESSONS (if <prior_run_lessons> is present in the user message): \
previous runs of this tool have produced consolidated lessons about which fix \
strategies work and which collapse to "unchanged". READ them and let them shape \
your proposals — e.g. if lessons say "narrow anchors + insert_after beat replace \
on multi-paragraph blocks", default to that. When a lesson directly influenced a \
proposal, list its ID in `lessons_applied`.

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
      "behavioral_probe": "Mid-workflow scenario that tests the fix",
      "lessons_applied": ["LSN-001"]
    }
  ]
}"""


# ── Pass 3: Fix Engine (LLM fallback) ────────────────────────────────────────

LLM_FIX_PROMPT = """\
You are a precise text editor. Apply the described fix to the prompt section below.

Fix description: {fix_description}
New content to incorporate: {new_content}

Return ONLY the modified section text. Do not add explanations or markup. \
Keep all surrounding text unchanged — only modify what the fix requires."""


# ── Pass 3b: Assertion check ─────────────────────────────────────────────────

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


# ── Pass 4: Behavioral Probe + Judge ─────────────────────────────────────────

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


# ── Pass 6: Knowledge-graph consolidation ────────────────────────────────────

CONSOLIDATION_PROMPT = """\
You are maintaining a small, evolving knowledge graph of what works and what \
doesn't when fixing conversational-agent prompts. You receive:

1. The current KG (lessons + triples) — may be empty on first run.
2. A single RunRecord summarizing what just happened in the latest run.
3. Structured per-issue outcomes (validations + verdicts).

Your job is to output an UPDATED KG that integrates the new evidence.

RULES:
- Lessons are human-readable rules at most ~220 characters each. State the \
rule, then a short `(N/M runs)` support tag, then a confidence tag.
- Triples are (head, relation, tail) facts with a numeric support count. Use \
structured heads like `fix_strategy:"replace"`, `verdict:"unchanged"`, \
`issue_pattern:"date_format_mismatch"`, `principle:"TOOL-04"`, \
`domain:"healthcare"`. Relations: `leads_to`, `best_fixed_by`, \
`co_occurs_with`, `fuzzy_match_rate`, `applies_in_domain`.
- MERGE: if a new observation matches an existing lesson or triple, \
increment support and update last_seen; do not duplicate.
- CONTRADICT: if the latest run contradicts an existing lesson, DOWNGRADE \
its confidence (high→medium→low) and note the change.
- RETIRE: lessons at "low" confidence with no support bump in the latest \
run should be dropped (list their IDs in retired_lesson_ids).
- CAP: max 30 lessons, max 150 triples. Trim the least-supported / oldest \
first.
- NEW lesson IDs continue the numbering sequence (highest existing + 1).

Be concrete. Lessons should name the FIX STRATEGY or OBSERVATION, not just \
the issue. Bad: "date format bugs happen". Good: "Fixing date-format bugs via \
insert_after near the tool-call paragraph beats replace on the whole tool \
section (3/3 runs). Confidence: medium".

Return JSON:
{
  "lessons": [ {"id": "LSN-001", "text": "...", "tags": ["healthcare", "TOOL-04"], "confidence": "medium", "support": 3, "last_seen": "2026-04-15"}, ... ],
  "triples": [ {"head": "...", "relation": "...", "tail": "...", "support": 2, "last_seen": "2026-04-15"}, ... ],
  "new_lesson_ids": ["LSN-007"],
  "retired_lesson_ids": []
}"""
