"""Pass 3: Anchor-based fix application + assertion validation.

Anchor resolution tries exact match → context-disambiguated duplicate →
fuzzy match → LLM fallback on a local window. Ambiguous anchors are
refused rather than guessed. Each applied fix is validated against its
assertion (first deterministically, then LLM if needed).
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Optional

from core.loader import KNOWN_KEYS
from core.models import AssertionCheck, FixProposal, FixValidation

from .llm import llm
from .prompts import ASSERTION_CHECK_PROMPT, LLM_FIX_PROMPT

logger = logging.getLogger(__name__)

# ── Tunable thresholds ───────────────────────────────────────────────────────

# Minimum similarity ratio for a fuzzy anchor match to be accepted as a fix site.
# Below this the fix falls through to the LLM fallback.
_FUZZY_THRESHOLD = 0.75

# Minimum similarity for the LLM fallback to locate a 1,500-char edit window.
# Lower than _FUZZY_THRESHOLD intentionally — we just need a rough neighbourhood.
_FUZZY_LLM_THRESHOLD = 0.50

# If a paragraph block is wider than this many chars, fall back to single-line
# matching to avoid replacing a wall of text for a narrow anchor.
_MAX_BLOCK_SIZE = 2000

# Half-width of the local context window handed to the LLM fallback editor.
_LLM_WINDOW_HALF = 750

# How many characters of new_content to check as a quick assertion shortcut
# before falling back to the LLM assertion verifier.
_ASSERTION_CONTENT_PREFIX = 80


# ── Anchor primitives ────────────────────────────────────────────────────────

def _find_block_boundaries(text: str, pos: int) -> tuple[int, int]:
    """Return (start, end) of the paragraph/block containing position `pos`."""
    block_start = text.rfind("\n\n", 0, pos)
    block_start = block_start + 2 if block_start != -1 else 0

    block_end = text.find("\n\n", pos)
    block_end = block_end if block_end != -1 else len(text)

    if block_end - block_start > _MAX_BLOCK_SIZE:
        single_start = text.rfind("\n", 0, pos)
        single_start = single_start + 1 if single_start != -1 else 0
        single_end = text.find("\n", pos)
        single_end = single_end if single_end != -1 else len(text)
        if single_end - single_start < block_end - block_start:
            block_start, block_end = single_start, single_end

    return block_start, block_end


def _fuzzy_find(
    text: str, anchor: str, threshold: float = _FUZZY_THRESHOLD
) -> tuple[int, float]:
    """Best approximate match for `anchor` in `text`. Returns (pos, ratio)."""
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
    """Pick the occurrence whose surrounding block best matches `context_hint`.

    Returns None if no clear winner — we refuse to guess since silent wrong
    edits are worse than a visible fix-failed.
    """
    if not context_hint or not context_hint.strip():
        return None

    hint_tokens = {
        t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", context_hint)
    }
    if not hint_tokens:
        return None

    scores: list[tuple[int, int]] = []
    for pos in positions:
        block_start, block_end = _find_block_boundaries(text, pos)
        window = text[max(0, block_start - 200):min(len(text), block_end + 200)]
        window_tokens = {
            t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", window)
        }
        scores.append((len(hint_tokens & window_tokens), pos))

    scores.sort(reverse=True)
    best_score, best_pos = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else -1

    if best_score == 0 or best_score == second_score:
        return None
    return best_pos


# ── Single-fix application ───────────────────────────────────────────────────

def _apply_single_fix(
    text: str, proposal: FixProposal
) -> tuple[str, str, Optional[float], str]:
    """Apply one fix. Returns (modified_text, method, confidence, reason)."""
    anchor = proposal.anchor_text

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
        else:
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]
        return text, "exact_anchor", None, ""

    fuzzy_pos, ratio = _fuzzy_find(text, anchor)
    if fuzzy_pos != -1:
        block_start, block_end = _find_block_boundaries(text, fuzzy_pos)
        if proposal.fix_type == "replace":
            text = text[:block_start] + proposal.new_content + text[block_end:]
        else:
            text = text[:block_end] + "\n\n" + proposal.new_content + text[block_end:]
        return text, "fuzzy_anchor", ratio, ""

    return text, "failed", None, f"Anchor not found (best fuzzy ratio: {ratio:.2f})."


def _llm_assisted_fix(text: str, proposal: FixProposal) -> Optional[str]:
    """Last-resort fallback: ask an LLM to edit a ~1500-char local window."""
    pos, _ratio = _fuzzy_find(text, proposal.anchor_text, threshold=_FUZZY_LLM_THRESHOLD)
    if pos == -1:
        pos = len(text) // 2

    context_start = max(0, pos - _LLM_WINDOW_HALF)
    context_end = min(len(text), pos + _LLM_WINDOW_HALF)
    section = text[context_start:context_end]

    system = LLM_FIX_PROMPT.format(
        fix_description=proposal.fix_description,
        new_content=proposal.new_content,
    )
    user = f"Prompt section to modify:\n\n{section}"

    result = llm.call(system, user, max_tokens=4096)

    if result and len(result) > len(section) * 0.3:
        return text[:context_start] + result + text[context_end:]
    return None


# ── Public apply + validation ────────────────────────────────────────────────

def apply_fixes(
    original_json: dict,
    prompt_text: str,
    proposals: list[FixProposal],
    selected_ids: list[str],
) -> tuple[dict, str, list[str], list[FixValidation]]:
    """Apply the selected proposals; validate each via its assertion."""
    selected = [p for p in proposals if p.issue_id in selected_ids]
    text = prompt_text
    applied: list[str] = []
    validations: list[FixValidation] = []

    for proposal in selected:
        new_text, method, confidence, failure_reason = _apply_single_fix(text, proposal)

        if method == "failed":
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

        validation = _check_assertion(text, proposal, method)
        if confidence is not None:
            validation.match_confidence = confidence
        validations.append(validation)

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


def _check_assertion(
    fixed_text: str, proposal: FixProposal, method: str
) -> FixValidation:
    """Check whether a fix's assertion holds in the fixed prompt text."""
    assertion = proposal.assertion

    key_phrases = extract_key_phrases(assertion)
    simple_pass = any(phrase.lower() in fixed_text.lower() for phrase in key_phrases)

    if simple_pass:
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=True,
            explanation=f"Key content found in fixed prompt (matched: {key_phrases[0][:50]}...).",
        )

    if proposal.new_content[:_ASSERTION_CONTENT_PREFIX] in fixed_text:
        return FixValidation(
            issue_id=proposal.issue_id,
            applied=True,
            method=method,
            assertion_passed=True,
            explanation="New content successfully inserted into prompt.",
        )

    pos = fixed_text.find(proposal.anchor_text)
    if pos == -1:
        pos, _ratio = _fuzzy_find(fixed_text, proposal.anchor_text, threshold=_FUZZY_LLM_THRESHOLD)
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


def extract_key_phrases(assertion: str) -> list[str]:
    """Pull testable key phrases (quoted strings, snake_case terms, formats) from an assertion."""
    phrases: list[str] = []

    quoted = re.findall(r'"([^"]+)"', assertion)
    phrases.extend(quoted)
    quoted_single = re.findall(r"'([^']+)'", assertion)
    phrases.extend(quoted_single)

    tool_terms = re.findall(r'\b[a-z_]+(?:_[a-z_]+)+\b', assertion)
    phrases.extend(tool_terms)

    formats = re.findall(r'[A-Z]{2,4}-[A-Z]{2,4}-[A-Z]{2,4}', assertion)
    phrases.extend(formats)

    seen: set[str] = set()
    unique: list[str] = []
    for p in phrases:
        if p.lower() not in seen and len(p) >= 3:
            seen.add(p.lower())
            unique.append(p)

    return unique if unique else [assertion[:60]]
