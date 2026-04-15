"""Pass 4 — Behavioral Probe + Judge prompts."""

from __future__ import annotations

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
