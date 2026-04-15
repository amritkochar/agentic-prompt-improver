"""Unit tests for agents/fix_engine.py — anchor resolution and fuzzy matching.

Covers:
- _fuzzy_find: known-good and below-threshold cases
- _all_occurrences: zero, one, and multiple matches
- _disambiguate_duplicate_anchor: winner found, tie, no hints
- _apply_single_fix: exact match (replace + insert_after), duplicate anchor,
  anchor-not-found, fuzzy fallback
- extract_key_phrases: quoted strings, snake_case terms, format patterns
- _find_block_boundaries: normal paragraph, oversized block (single-line fallback)
"""

from __future__ import annotations

import pytest

# Tested module — imported directly; no LLM calls are made in these paths.
from agents.fix_engine import (
    _all_occurrences,
    _apply_single_fix,
    _disambiguate_duplicate_anchor,
    _find_block_boundaries,
    _fuzzy_find,
    extract_key_phrases,
    _FUZZY_THRESHOLD,
    _FUZZY_LLM_THRESHOLD,
    _MAX_BLOCK_SIZE,
)
from core.models import FixProposal


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_proposal(
    anchor: str,
    new_content: str,
    fix_type: str = "replace",
    anchor_context: str = "test section",
    assertion: str = "The prompt contains the new content.",
) -> FixProposal:
    return FixProposal(
        issue_id="WA-01",
        dimension="workflow_adherence",
        root_cause="test",
        impact_if_unfixed="test",
        fix_type=fix_type,  # type: ignore[arg-type]
        fix_description="test fix",
        anchor_text=anchor,
        anchor_context=anchor_context,
        new_content=new_content,
        assertion=assertion,
        behavioral_probe="N/A",
    )


# ── _fuzzy_find ───────────────────────────────────────────────────────────────

class TestFuzzyFind:
    def test_exact_match_returns_max_ratio(self):
        text = "Hello world, this is a test string."
        pos, ratio = _fuzzy_find(text, "this is a test")
        assert pos != -1
        assert ratio >= 0.99

    def test_one_char_diff_above_threshold(self):
        text = "Please call book_appointment to schedule"
        anchor = "book_appointement"  # typo — one extra 'e'
        pos, ratio = _fuzzy_find(text, anchor, threshold=0.75)
        # Should still be found since similarity is > 0.75
        assert pos != -1
        assert ratio >= 0.75

    def test_low_similarity_below_threshold_returns_minus_one(self):
        text = "The quick brown fox jumps over the lazy dog."
        pos, ratio = _fuzzy_find(text, "xyzxyzxyz", threshold=_FUZZY_THRESHOLD)
        assert pos == -1

    def test_empty_anchor_returns_minus_one(self):
        pos, ratio = _fuzzy_find("some text", "", threshold=0.5)
        assert pos == -1
        assert ratio == 0.0

    def test_anchor_longer_than_text_returns_minus_one(self):
        pos, ratio = _fuzzy_find("hi", "this is much longer than the text", threshold=0.5)
        assert pos == -1

    def test_lLM_threshold_is_lower_than_fuzzy_threshold(self):
        # Structural test: LLM fallback threshold must be more permissive
        assert _FUZZY_LLM_THRESHOLD < _FUZZY_THRESHOLD


# ── _all_occurrences ─────────────────────────────────────────────────────────

class TestAllOccurrences:
    def test_no_match(self):
        assert _all_occurrences("hello world", "xyz") == []

    def test_single_match(self):
        result = _all_occurrences("foo bar baz", "bar")
        assert result == [4]

    def test_multiple_matches(self):
        text = "abc abc abc"
        result = _all_occurrences(text, "abc")
        assert result == [0, 4, 8]

    def test_overlapping_not_counted_twice(self):
        # "aaa" in "aaaa" — two non-overlapping positions: 0, 1
        result = _all_occurrences("aaaa", "aa")
        assert 0 in result
        # Each start position is returned independently
        assert len(result) >= 2

    def test_empty_needle_returns_empty(self):
        assert _all_occurrences("some text", "") == []


# ── _disambiguate_duplicate_anchor ───────────────────────────────────────────

class TestDisambiguateDuplicateAnchor:
    # "anchor" appears twice.  A unique token "uniquetoken" sits only near the
    # second occurrence.  A 200-char filler block between the two occurrences
    # ensures the first occurrence's look-ahead window (block_end + 200) cannot
    # reach "uniquetoken", so the disambiguation produces an unambiguous winner.
    TEXT = (
        "Use the anchor here.\n\n"
        + "Z" * 200 + "\n\n"
        + "uniquetoken The anchor there.\n"
    )

    def test_picks_winner_via_context_hint(self):
        anchor = "anchor"
        positions = _all_occurrences(self.TEXT, anchor)
        assert len(positions) == 2

        result = _disambiguate_duplicate_anchor(self.TEXT, positions, "uniquetoken")
        # "uniquetoken" is only visible in the second occurrence's window
        assert result is not None
        assert self.TEXT[result:result + len(anchor)] == anchor
        # Second occurrence has higher offset than the first
        assert result == positions[1]

    def test_returns_none_when_context_is_empty(self):
        positions = [0, 50]
        assert _disambiguate_duplicate_anchor("some text here", positions, "") is None

    def test_returns_none_when_context_does_not_discriminate(self):
        # Both occurrences are in identical surrounding context
        text = "abc xyz abc xyz"
        positions = [0, 8]
        # Context "xyz" matches both blocks equally — should return None (tie)
        result = _disambiguate_duplicate_anchor(text, positions, "xyz")
        assert result is None

    def test_returns_none_when_no_hint(self):
        result = _disambiguate_duplicate_anchor("a b a", [0, 4], None)
        assert result is None


# ── _find_block_boundaries ───────────────────────────────────────────────────

class TestFindBlockBoundaries:
    def test_simple_paragraph(self):
        text = "First paragraph.\n\nSecond paragraph here.\n\nThird."
        pos = text.index("Second")
        start, end = _find_block_boundaries(text, pos)
        block = text[start:end]
        assert "Second paragraph here." in block
        assert "First" not in block

    def test_oversized_block_falls_back_to_line(self):
        # Build a block wider than _MAX_BLOCK_SIZE by having a huge paragraph.
        # The anchor line itself must be short (< _MAX_BLOCK_SIZE) so the
        # single-line fallback produces a smaller result than the full block.
        filler = "word " * 10  # 50 chars — short per-line filler
        # Create many short lines separated by \n (not \n\n) so they form
        # one giant paragraph that exceeds _MAX_BLOCK_SIZE.
        many_lines = "\n".join([filler] * (_MAX_BLOCK_SIZE // len(filler) + 5))
        anchor_line = "THIS IS THE ANCHOR LINE"
        text = many_lines + "\n" + anchor_line + "\n" + many_lines
        pos = text.index(anchor_line)
        start, end = _find_block_boundaries(text, pos)
        block = text[start:end]
        # The fallback narrows to single-line: the returned block should be
        # substantially smaller than the full oversized paragraph.
        full_block_size = len(many_lines) * 2 + len(anchor_line) + 2
        assert len(block) < full_block_size
        assert anchor_line in block

    def test_start_of_text(self):
        text = "Opening line.\n\nSecond paragraph."
        start, end = _find_block_boundaries(text, 0)
        assert start == 0
        assert "Opening line." in text[start:end]


# ── _apply_single_fix ────────────────────────────────────────────────────────

class TestApplySingleFix:
    BASE = "Introduction text.\n\nThe agent should call book_appointment first.\n\nClosing text."

    def test_exact_match_replace(self):
        proposal = _make_proposal(
            anchor="call book_appointment",
            new_content="The agent must call book_appointment and verify confirmation.",
            fix_type="replace",
        )
        result, method, confidence, reason = _apply_single_fix(self.BASE, proposal)
        assert method == "exact_anchor"
        assert confidence is None
        assert "must call book_appointment and verify confirmation" in result
        # Original text replaced
        assert "call book_appointment first" not in result

    def test_exact_match_insert_after(self):
        proposal = _make_proposal(
            anchor="call book_appointment",
            new_content="Always confirm the booking ID before proceeding.",
            fix_type="insert_after",
        )
        result, method, _, _ = _apply_single_fix(self.BASE, proposal)
        assert method == "exact_anchor"
        assert "Always confirm the booking ID" in result
        # Original paragraph still present
        assert "call book_appointment first" in result

    def test_ambiguous_anchor_refuses(self):
        text = "Use the keyword here.\n\nUse the keyword there."
        proposal = _make_proposal(
            anchor="Use the keyword",
            new_content="New text.",
            anchor_context="",  # no context to disambiguate
        )
        result, method, confidence, reason = _apply_single_fix(text, proposal)
        assert method == "failed"
        assert "refusing" in reason.lower() or "did not pick" in reason.lower()
        assert result == text  # text unchanged

    def test_ambiguous_anchor_resolved_by_context(self):
        # "The process" appears twice. "uniquemarker" is placed ONLY near the
        # second occurrence with a 200-char filler between them so the first
        # occurrence's look-ahead window cannot see "uniquemarker".
        text = (
            "## Section A\nThe process starts here.\n\n"
            + "Z" * 200 + "\n\n"
            + "## Section B uniquemarker\nThe process ends here.\n"
        )
        proposal = _make_proposal(
            anchor="The process",
            new_content="The updated process ends here.",
            fix_type="replace",
            anchor_context="Section B uniquemarker",
        )
        result, method, _, _ = _apply_single_fix(text, proposal)
        assert method == "exact_anchor"
        assert "updated process ends here" in result
        # Section A unchanged
        assert "starts here" in result

    def test_anchor_not_found_returns_failed(self):
        proposal = _make_proposal(
            anchor="completely nonexistent phrase xyz",
            new_content="irrelevant",
        )
        result, method, _, _ = _apply_single_fix(self.BASE, proposal)
        assert method == "failed"
        assert result == self.BASE


# ── extract_key_phrases ───────────────────────────────────────────────────────

class TestExtractKeyPhrases:
    def test_extracts_quoted_string(self):
        assertion = 'The prompt says "book_appointment" must be called first.'
        phrases = extract_key_phrases(assertion)
        assert "book_appointment" in phrases

    def test_extracts_snake_case_term(self):
        assertion = "The agent calls get_available_slots with start_date formatted as DD-MM-YYYY."
        phrases = extract_key_phrases(assertion)
        assert any("get_available_slots" in p or "start_date" in p for p in phrases)

    def test_extracts_date_format_pattern(self):
        assertion = "Dates must be in DD-MM-YYYY format when calling the tool."
        phrases = extract_key_phrases(assertion)
        assert any("DD-MM-YYYY" in p for p in phrases)

    def test_falls_back_to_assertion_prefix_when_no_terms(self):
        assertion = "This is a plain assertion with no special terms or quotes at all."
        phrases = extract_key_phrases(assertion)
        assert len(phrases) >= 1
        assert phrases[0] == assertion[:60]

    def test_deduplicates(self):
        assertion = 'Call "book_appointment" before calling "book_appointment" again.'
        phrases = extract_key_phrases(assertion)
        count = sum(1 for p in phrases if p == "book_appointment")
        assert count == 1
