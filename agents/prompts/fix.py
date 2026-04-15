"""Pass 3 — Fix Engine prompts (LLM fallback + assertion check)."""

from __future__ import annotations

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
