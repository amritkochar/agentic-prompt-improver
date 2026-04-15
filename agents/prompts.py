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

4. active_principles — principles this specific prompt VISIBLY VIOLATES or
   is at clear, concrete risk of violating given its domain and tool set.
   MAXIMUM 12. MINIMUM is whatever the evidence supports — 3 sharp entries
   beats 12 speculative ones. "Load-bearing" means: if this principle were
   violated, a production call would measurably break or degrade. Do NOT
   include a principle just because it is theoretically relevant; skip any
   that the prompt clearly already satisfies.

   For each entry:
   - id: the principle ID (e.g. "STYLE-01", "TOOL-04", "CONTENT-01")
   - reason: one short sentence naming the CONCRETE feature of the prompt
     that makes this principle load-bearing. Quote a fragment of the prompt
     or name a specific tool/section. Bad: "The prompt is a voice agent so
     STYLE-01 applies." Good: "Instruction 'Please confirm each of the
     following before proceeding: ...' lists 4 items in one voice reply —
     STYLE-01 violation risk."

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
- Quote the EXACT text from the prompt as evidence (copy-paste verbatim). If you \
cannot quote verbatim text that proves the issue, the issue is speculative — drop it.
- Rate severity: critical (breaks behavior silently), high (user-facing problem), \
medium (quality degradation), low (minor improvement).
- Assign an ID: WA-01, WA-02, ... for workflow adherence; PE-01, PE-02, ... for \
caller experience; PRIN-<PRINCIPLE-ID> for principle violations. Number from 01 \
within each dimension.
- REPRODUCIBILITY TEST: before raising an issue, imagine a concrete mid-workflow \
scenario (≤3 sentences) where a capable agent following this prompt would produce \
visibly wrong behavior because of the issue. If you cannot construct such a scenario, \
the issue is not testable and should be dropped — our downstream verification \
requires it.
- NO PADDING: fewer sharp issues beat many soft ones. Do not raise stylistic \
preferences, hypothetical edge cases, or issues the prompt already mitigates \
elsewhere. If in doubt, leave it out.

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

A shorter, sharper list is better than a longer speculative one. Be willing \
to cut the list in half if that's what the evidence supports.

REMOVE any issues that are:
- False positives (the prompt actually handles it correctly elsewhere — check carefully)
- Stylistic preferences rather than real quality problems
- Duplicates of other issues in the list
- Too vague or speculative to be actionable
- Principles issues raised against a principle that the prompt does NOT actually violate
- Domain-inapplicable (e.g. flagging a missing "eligibility check" on a non-scheduling agent)
- Not reproducible as a concrete mid-workflow scenario (downstream verification \
cannot probe abstractions)

MERGE issues that are different surface symptoms of the same root cause into a \
single sharper issue. Prefer one issue with three pieces of evidence over three \
issues with one each.

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

2. ANCHOR TEXT (anchor_text): A short (20-40 character), UNIQUE substring in the \
prompt copied VERBATIM, AT or NEAR where the fix should go. This is used to locate \
the edit site deterministically.
   CRITICAL: The anchor must appear EXACTLY ONCE in the prompt. Before committing, \
scan the prompt for every occurrence of your candidate string. If it appears \
multiple times, extend it until it is unique, or pick a different nearby phrase. \
Our fix engine REFUSES to guess when anchors are ambiguous — a non-unique anchor \
fails the fix cleanly, no second chance.

3. ANCHOR CONTEXT (anchor_context): Which section of the prompt this is in (for readability).

4. FIX TYPE (fix_type):
   - "replace": Replace the paragraph/sentence containing the anchor with new_content.
   - "insert_after": Insert new_content as a new paragraph AFTER the paragraph containing the anchor.

5. NEW CONTENT (new_content): The replacement text or the new text to add. Minimal, \
surgical, and stylistically INVISIBLE — match the prompt's existing tense, register, \
bullet-vs-prose shape, and capitalization conventions. For "replace" fixes, include \
the FULL replacement for the paragraph/block being changed. For "insert_after" \
fixes, include only the new text to add. Do NOT rewrite more than necessary; do \
NOT restructure adjacent paragraphs. Your fix must respect the active principles \
listed in the <principles_brief> block — e.g. for voice modality, keep added text \
brief and transactional; for SMS content, include the 5-Ws; for multi-step side \
effects, specify rollback.

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

   ADVERSARIAL FRAMING (CRITICAL — this is the #1 reason verifications come back \
"inconclusive"): A capable simulator model will often do the right thing by reading \
tool schemas or applying general common sense, even when the prompt is silent or \
wrong on the rule. If your probe does not make the WRONG behavior the LOCALLY \
TEMPTING answer, both the original and fixed agent will produce the same correct \
output and the judge will rule "inconclusive" (a wasted verification).

   PROBE DESIGN CHECKLIST — every probe must satisfy ALL of these:
   (a) **Adversarial lure**: the scenario contains at least one detail that makes the \
BUGGY path look locally correct. Without the lure, the original prompt's bug will not \
surface. State the lure explicitly as part of the scenario facts.
   (b) **Mid-workflow positioning**: identity verification, greeting, and any \
gate-keeping steps are already complete. The agent is AT the decision point where \
the bug lives.
   (c) **Schema invisibility**: the correct answer must NOT be deducible from the \
tool schema alone. If a well-designed tool schema (param names, enums, required \
fields) would guide any reasonable agent to the correct answer regardless of the \
prompt, the probe cannot distinguish original from fixed. Either (i) change the \
lure so the schema's default answer is WRONG for this scenario, or (ii) pick a \
different issue to probe.
   (d) **Single decision point**: ask about ONE next action, not a 5-step plan. \
Multi-step answers dilute the signal and let the judge see "both prompts eventually \
do the right thing" even when the first step differs.

   Before committing the probe, answer in your head: "If a smart simulator \
ignored the prompt text entirely and just looked at the scenario + tool schema, \
would it produce the BUGGY answer?" If no, the probe is too easy — strengthen \
the lure or pick a different issue.

   Concrete lure patterns by bug class:
     - **Format bugs**: supply the data in a DIFFERENT format than the tool \
requires (e.g. "current_time returned 2026-04-14; the patient said June 5th 2026" \
— invites copying YYYY-MM-DD when the tool wants DD-MM-YYYY).
     - **Ordering bugs**: make the wrong-order path look equally natural (e.g. \
"the patient said yes to the new slot" — invites immediate cancel-then-book).
     - **Missing-parameter bugs**: describe the scenario without naming the \
parameter, so the original prompt's omission is the only thing stopping the agent \
from forgetting it. If the tool schema marks the param `required`, the schema \
alone will save both prompts — pick a different bug or a param the schema \
treats as optional.
     - **Workflow-sequence bugs**: end the scenario at the precise step BEFORE \
the one that is usually skipped, not after it.
     - **Empathy/tone bugs**: put the caller in an emotionally loaded moment and \
ask for the agent's NEXT utterance only. A multi-step plan dilutes the speech \
signal.
     - **Guardrail bugs (off-policy requests)**: make the off-policy ask sound \
routine and embed it in a legitimate request, so refusing is the less \
"cooperative-seeming" path.

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
You are a precise text editor applying a single surgical fix to a section of a \
conversational-agent system prompt.

Fix description: {fix_description}
New content to incorporate: {new_content}

RULES:
- Return ONLY the modified section text. No explanations, no preamble, no trailing \
commentary, no markdown code fences (```), no XML tags.
- Keep ALL surrounding text byte-for-byte unchanged. Change only what the fix \
strictly requires. Do not reword neighboring sentences, reflow paragraphs, or \
normalize whitespace.
- Preserve the original tense, register, bullet-vs-prose shape, and capitalization.
- If the fix cannot be applied to this section without rewriting more than one \
paragraph of unrelated text, return the section EXACTLY as given, unchanged. A \
visible fix-failed is safer than silent collateral damage."""


# ── Pass 3b: Assertion check ─────────────────────────────────────────────────

ASSERTION_CHECK_PROMPT = """\
You are a strict verifier checking whether a specific assertion about a \
conversational-agent prompt is satisfied by the prompt text.

Read the prompt section below and determine whether the assertion is true.

ASSERTION: {assertion}

Rules:
- Be strict. The assertion must be clearly and specifically satisfied by VERBATIM \
text in the prompt — not merely implied, not partially addressed, not something a \
reader could reasonably infer.
- Partial satisfaction is NOT a pass. If the assertion has two clauses and only \
one is supported by the text, return passed=false and name which clause is \
missing.
- In `explanation`, quote the exact text that satisfies the assertion (pass) or \
state "not found in section — closest match: '<nearest phrase>'" (fail).

Return JSON:
{{
  "passed": true_or_false,
  "explanation": "Verbatim quote that satisfies the assertion, or 'not found' reason"
}}"""


# ── Pass 4: Behavioral Probe + Judge ─────────────────────────────────────────

PROBE_PROMPT = """\
You are role-playing a deployed agent that follows the system prompt below \
LITERALLY. You are in the MIDDLE of an ongoing interaction — greeting, identity \
verification, and earlier workflow steps have already been completed.

CRITICAL — PROMPT FIDELITY: Your job is to do exactly what THIS system prompt \
tells you to do, including any mistakes, omissions, or ambiguities it contains. \
Do NOT silently apply general common sense to patch over bugs you notice. Do NOT \
reformat parameters based on what looks reasonable. Do NOT add safety checks the \
prompt doesn't mention. If the prompt is silent on something, follow the most \
natural reading of what it does say — even when that produces a clearly wrong \
result. We are measuring the prompt, not your intelligence.

Use the modality stated in the <principles_brief> (if present) to shape your \
reply style: for voice, keep SPEECH brief and transactional; for chat, short \
structured replies are fine; for SMS, ultra-terse with the 5-Ws where applicable.

The scenario below describes what has happened so far and the exact decision \
point you are at.

Given this state, describe the ONE next action you take. Be SPECIFIC about:

SPEECH: [Exactly what you say to the caller — full verbatim utterance, not a \
summary. If you would stay silent, write "none".]
TOOL_CALLS: [List each tool call you would make at this step, with exact function \
name and ALL parameters as JSON (values in the format the prompt tells you to use, \
not the format that seems "more standard"). If no tool call is needed, write "none".]
CONDITIONS_CHECKED: [List every condition, rule, or constraint you actually \
consulted in the prompt before deciding. Include dates compared, limits verified, \
provider restrictions, enum/format requirements, etc. If none, write "none". Do \
NOT list conditions the prompt never mentioned.]

Do NOT include meta-commentary, apologies, or "I noticed the prompt says X but it \
should probably say Y" — just the three sections above."""

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
4-6. Do NOT call partial wins "improved". If you find yourself writing "the fix \
improves X but still misses Y" → verdict is "unchanged", score 4-6.

FOCUS ON CONCRETE DIFFERENCES between original and fixed:
- TOOL_CALLS parameters — did the right params get added/changed/removed with the \
right values? (e.g. `late_cancel: true` vs missing; `start_date: "2026-06-05"` vs \
`"05-06-2026"`)
- CONDITIONS_CHECKED — did the agent actually consult something it previously \
skipped, or was the new entry just restated intent without affecting the action?
- Operation ordering — did the sequence of tool calls change as the issue required?
- SPEECH — for caller-experience issues, did tone / empathy / escalation path \
change in SUBSTANCE, not just word choice?

IGNORE differences that are pure surface style: synonym choices, sentence \
ordering within the same meaning, verbosity variation, politeness phrasing that \
doesn't change the routing or the information conveyed. A fix that only changes \
how things are phrased without changing what the agent DOES is "unchanged".

NONSENSE / TRUNCATION: if either behavior is incomplete, garbled, or clearly \
failed to follow the PROBE format (missing SPEECH/TOOL_CALLS/CONDITIONS_CHECKED), \
verdict is "inconclusive" and `remaining_concerns` must call out the truncation.

Be skeptical and precise. If the original already produced the correct output, \
choose "inconclusive" — do not reward the fix for matching behavior that was \
already right. The verdict measures whether the FIX moved the agent, not whether \
the agent's final answer is correct.

Return JSON:
{
  "verdict": "improved" | "inconclusive" | "unchanged" | "regressed",
  "improvement_score": 1-10,
  "explanation": "Quote the specific tool parameters, conditions, or speech that did or didn't change, and state why the verdict fits.",
  "remaining_concerns": "What still needs fixing (if unchanged/regressed), what a sharper probe should test (if inconclusive), or null (if improved)"
}"""

FOLLOWUP_GENERATOR_PROMPT = """\
You are simulating a caller who is probing an agent for a specific, named \
quality issue. You have already sent one message and received one response. \
Write the caller's NEXT message — a short, natural, on-topic follow-up that \
keeps the pressure on the SAME decision point the original probe was targeting.

The follow-up must:
- Stay in character as the caller. If the caller was frustrated, stay frustrated; \
if they were in a hurry, keep that urgency.
- React to something CONCRETE the agent just said (agree, disagree, add a \
detail, ask a clarifying question).
- Keep exercising the exact failure mode being tested. If the issue is about \
date format, the follow-up must still involve date handling. Do NOT drift to \
unrelated parts of the workflow (don't start asking about insurance when the \
probe was about scheduling).
- Introduce a NEW adversarial angle on the same bug when possible — e.g. the \
agent gave a correct-looking answer; the follow-up adds a wrinkle that re-opens \
the bug (a second date, a corrected name, a conflicting detail).
- Be 1-3 sentences. No stage directions, no "the caller says", no quotation \
marks — just the utterance itself.

Return ONLY the caller's next message."""


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

CONSULTABILITY TEST: before writing a lesson or triple, ask: "Would a future \
analyze-pass prompt consult this to make a better fix proposal?" If the answer \
is no — it's a one-off observation, a run metric, or a trivia point — skip it. \
The KG only gets useful when every entry is actionable. Single-run \
(support=1) lessons must describe a clearly reusable pattern, not a one-off \
coincidence.

Return JSON:
{
  "lessons": [ {"id": "LSN-001", "text": "...", "tags": ["healthcare", "TOOL-04"], "confidence": "medium", "support": 3, "last_seen": "2026-04-15"}, ... ],
  "triples": [ {"head": "...", "relation": "...", "tail": "...", "support": 2, "last_seen": "2026-04-15"}, ... ],
  "new_lesson_ids": ["LSN-007"],
  "retired_lesson_ids": []
}"""
