"""Pass 6 — Knowledge-graph consolidation prompt."""

from __future__ import annotations

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
