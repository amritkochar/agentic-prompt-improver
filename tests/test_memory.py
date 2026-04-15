"""Unit tests for core/memory.py — KG round-trip, lesson selection, serialisation.

Covers:
- load_kg: missing file → empty KG, malformed lines silently skipped, valid parse
- write_kg + load_kg: round-trip preserves lessons and triples
- select_relevant_lessons: tag overlap scoring, max_out cap, untagged baseline
- format_lessons_for_prompt: empty list → empty string, populated → XML block
- _trim_lessons / _trim_triples: caps respected
"""

from __future__ import annotations

import pytest
from pathlib import Path

from core.memory import (
    load_kg,
    write_kg,
    select_relevant_lessons,
    format_lessons_for_prompt,
    MAX_LESSONS,
    MAX_TRIPLES,
)
from core.models import (
    KnowledgeGraph,
    Lesson,
    PrinciplesBrief,
    ActivePrinciple,
    Triple,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _lesson(
    lid: str,
    text: str,
    tags: list[str],
    confidence: str = "medium",
    support: int = 1,
    last_seen: str = "2026-04-01",
) -> Lesson:
    return Lesson(id=lid, text=text, tags=tags, confidence=confidence,
                  support=support, last_seen=last_seen)


def _brief(domain: str = "healthcare", modality: str = "voice") -> PrinciplesBrief:
    return PrinciplesBrief(
        modality=modality,  # type: ignore[arg-type]
        domain=domain,
        domain_signals=[domain],
        active_principles=[ActivePrinciple(id="TOOL-04", reason="test")],
        interaction_contract="Keep replies brief.",
        structure_notes="No variables in static block.",
    )


# ── load_kg ───────────────────────────────────────────────────────────────────

class TestLoadKg:
    def test_missing_file_returns_empty(self, tmp_path):
        kg = load_kg(tmp_path / "nonexistent.md")
        assert kg.lessons == []
        assert kg.triples == []

    def test_valid_lesson_parsed(self, tmp_path):
        md = tmp_path / "kg.md"
        md.write_text(
            "## Lessons\n\n"
            "- [LSN-001] Replace anchor beats fuzzy for small edits "
            "|tags=workflow_adherence, TOOL-04|confidence=medium|support=3|last_seen=2026-04-01\n"
        )
        kg = load_kg(md)
        assert len(kg.lessons) == 1
        assert kg.lessons[0].id == "LSN-001"
        assert kg.lessons[0].support == 3
        assert "TOOL-04" in kg.lessons[0].tags

    def test_malformed_lesson_line_skipped(self, tmp_path):
        md = tmp_path / "kg.md"
        md.write_text(
            "## Lessons\n\n"
            "- [LSN-001] Valid lesson |tags=x|confidence=high|support=1|last_seen=2026-01-01\n"
            "- malformed line with no pipe structure\n"
        )
        kg = load_kg(md)
        assert len(kg.lessons) == 1

    def test_valid_triple_parsed(self, tmp_path):
        md = tmp_path / "kg.md"
        md.write_text(
            "## Triples\n\n"
            "| Head | Relation | Tail | Support | Last seen |\n"
            "|---|---|---|---|---|\n"
            '| fix_strategy:"replace" | leads_to | "unchanged" | 2 | 2026-04-01 |\n'
        )
        kg = load_kg(md)
        assert len(kg.triples) == 1
        assert kg.triples[0].support == 2

    def test_invalid_confidence_normalised_to_medium(self, tmp_path):
        # The regex requires lowercase [a-z]+ for confidence.
        # A lowercase but unrecognised value ("weird") IS parsed but normalised
        # to "medium" by the load_kg validation branch.
        md = tmp_path / "kg.md"
        md.write_text(
            "## Lessons\n\n"
            "- [LSN-001] Some lesson |tags=x|confidence=weird|support=1|last_seen=2026-01-01\n"
        )
        kg = load_kg(md)
        assert len(kg.lessons) == 1
        assert kg.lessons[0].confidence == "medium"


# ── write_kg + load_kg round-trip ────────────────────────────────────────────

class TestRoundTrip:
    def _make_kg(self) -> KnowledgeGraph:
        return KnowledgeGraph(
            lessons=[
                _lesson("LSN-001", "Insert after beats replace on large blocks",
                        ["workflow_adherence", "TOOL-04"], confidence="high", support=5),
                _lesson("LSN-002", "Adversarial probes must have a lure",
                        ["verify", "probe_design"], confidence="medium", support=2),
            ],
            triples=[
                Triple(head='fix_strategy:"insert_after"', relation="leads_to",
                       tail='"improved"', support=3, last_seen="2026-04-01"),
            ],
        )

    def test_round_trip_lessons(self, tmp_path):
        path = tmp_path / "kg.md"
        kg = self._make_kg()
        write_kg(kg, path)
        loaded = load_kg(path)
        assert len(loaded.lessons) == 2
        ids = {l.id for l in loaded.lessons}
        assert "LSN-001" in ids
        assert "LSN-002" in ids

    def test_round_trip_lesson_fields(self, tmp_path):
        path = tmp_path / "kg.md"
        kg = self._make_kg()
        write_kg(kg, path)
        loaded = load_kg(path)
        lsn1 = next(l for l in loaded.lessons if l.id == "LSN-001")
        assert lsn1.confidence == "high"
        assert lsn1.support == 5
        assert "TOOL-04" in lsn1.tags

    def test_round_trip_triples(self, tmp_path):
        path = tmp_path / "kg.md"
        kg = self._make_kg()
        write_kg(kg, path)
        loaded = load_kg(path)
        assert len(loaded.triples) == 1
        assert loaded.triples[0].support == 3

    def test_caps_enforced_on_write(self, tmp_path):
        path = tmp_path / "kg.md"
        # Build more lessons than the cap
        lessons = [
            _lesson(f"LSN-{i:03d}", f"Lesson {i}", ["tag"], support=i)
            for i in range(1, MAX_LESSONS + 10)
        ]
        kg = KnowledgeGraph(lessons=lessons)
        write_kg(kg, path)
        loaded = load_kg(path)
        assert len(loaded.lessons) <= MAX_LESSONS

    def test_caps_enforced_on_triples(self, tmp_path):
        path = tmp_path / "kg.md"
        triples = [
            Triple(head=f"h{i}", relation="r", tail="t", support=1, last_seen="2026-01-01")
            for i in range(MAX_TRIPLES + 20)
        ]
        kg = KnowledgeGraph(triples=triples)
        write_kg(kg, path)
        loaded = load_kg(path)
        assert len(loaded.triples) <= MAX_TRIPLES


# ── select_relevant_lessons ───────────────────────────────────────────────────

class TestSelectRelevantLessons:
    def test_empty_kg_returns_empty(self):
        kg = KnowledgeGraph()
        result = select_relevant_lessons(kg, _brief(), issues=[])
        assert result == []

    def test_matching_tag_beats_non_matching(self):
        kg = KnowledgeGraph(lessons=[
            _lesson("LSN-001", "Relevant lesson", ["healthcare", "TOOL-04"], support=1),
            _lesson("LSN-002", "Irrelevant lesson", ["fintech", "fraud"], support=10),
        ])
        brief = _brief(domain="healthcare")
        result = select_relevant_lessons(kg, brief, issues=[])
        # LSN-001 matches healthcare; LSN-002 matches nothing in this run's tags
        ids = [l.id for l in result]
        assert "LSN-001" in ids
        # LSN-002 should be filtered out (zero overlap → score -1)
        assert "LSN-002" not in ids

    def test_max_out_respected(self):
        kg = KnowledgeGraph(lessons=[
            _lesson(f"LSN-{i:03d}", f"Lesson {i}", ["healthcare"], support=i)
            for i in range(1, 20)
        ])
        result = select_relevant_lessons(kg, _brief(), issues=[], max_out=3)
        assert len(result) <= 3

    def test_higher_support_ranked_first(self):
        kg = KnowledgeGraph(lessons=[
            _lesson("LSN-LOW", "Low support lesson", ["healthcare"], support=1),
            _lesson("LSN-HIGH", "High support lesson", ["healthcare"], support=10),
        ])
        result = select_relevant_lessons(kg, _brief(), issues=[])
        assert result[0].id == "LSN-HIGH"

    def test_untagged_lesson_gets_baseline_score(self):
        # An untagged lesson should not be dropped entirely (gets 0.5 baseline)
        kg = KnowledgeGraph(lessons=[
            _lesson("LSN-UNTAGGED", "Untagged lesson", tags=[], support=1),
        ])
        result = select_relevant_lessons(kg, _brief(), issues=[])
        # Untagged lesson should appear (baseline score 0.5 ≥ 0)
        assert len(result) == 1

    def test_none_brief_still_returns_results(self):
        kg = KnowledgeGraph(lessons=[
            _lesson("LSN-001", "Some lesson", ["healthcare"], support=2),
        ])
        result = select_relevant_lessons(kg, brief=None, issues=[])
        # With no brief, no run_tags — untagged or all-tagged lessons pass baseline
        # Lessons with tags but no matching run_tags get score -1 → filtered
        # This test just ensures no crash
        assert isinstance(result, list)


# ── format_lessons_for_prompt ─────────────────────────────────────────────────

class TestFormatLessonsForPrompt:
    def test_empty_returns_empty_string(self):
        assert format_lessons_for_prompt([]) == ""

    def test_populated_returns_xml_block(self):
        lessons = [
            _lesson("LSN-001", "Replace fails on big blocks", ["TOOL-04"], support=3),
        ]
        result = format_lessons_for_prompt(lessons)
        assert result.startswith("<prior_run_lessons>")
        assert result.strip().endswith("</prior_run_lessons>")
        assert "LSN-001" in result
        assert "Replace fails on big blocks" in result

    def test_confidence_and_support_included(self):
        lessons = [
            _lesson("LSN-002", "Adversarial lure required", ["probe"], confidence="high", support=5),
        ]
        result = format_lessons_for_prompt(lessons)
        assert "high" in result
        assert "support=5" in result
