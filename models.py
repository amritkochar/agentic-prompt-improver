"""Pydantic data models for all inter-pass data."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Dimension = Literal["patient_experience", "workflow_adherence", "principles"]

Modality = Literal["voice", "chat", "sms", "mixed", "unknown"]

LessonId = str
Confidence = Literal["low", "medium", "high"]


class ActivePrinciple(BaseModel):
    id: str           # "STYLE-01", "TOOL-04", etc.
    reason: str       # why this principle is load-bearing for THIS prompt


class PrinciplesBrief(BaseModel):
    """Pass 0 output: an adaptive quality lens for the specific input prompt."""

    modality: Modality
    domain_signals: list[str]                 # e.g. ["healthcare", "scheduling", "sms"]
    # Primary domain classification used by the non-healthcare warning gate.
    # "healthcare" is the tool's optimized domain; anything else triggers a notice.
    domain: str = "unknown"
    active_principles: list[ActivePrinciple]  # subset of canonical library
    interaction_contract: str                 # one-paragraph tailored contract
    structure_notes: str                      # cacheability / layout observations
    # Compact registry of tool param formats/enums/required fields. Populated
    # deterministically from tool definitions (not by the LLM) so downstream
    # passes receive structured cross-reference data.
    tool_schema_registry: Optional[str] = None


class Issue(BaseModel):
    id: str                                      # "WA-01", "PE-03", "PRIN-STYLE-01"
    dimension: Dimension
    severity: Literal["critical", "high", "medium", "low"]
    title: str
    description: str                             # What the problem is and why it matters
    evidence: str                                # Verbatim quote from prompt
    location_hint: str                           # Which section


class FixProposal(BaseModel):
    issue_id: str
    # Propagated from the source Issue for verify-routing. Optional so the LLM
    # can omit it; analyze() post-fills it from the source Issue list.
    dimension: Optional[Dimension] = None
    root_cause: str
    impact_if_unfixed: str
    fix_type: Literal["replace", "insert_after"]
    fix_description: str
    anchor_text: str              # 20-40 char unique substring near edit site
    anchor_context: str           # section name for human readability
    new_content: str              # text to insert or replace with
    assertion: str                # verifiable claim about the fixed prompt
    behavioral_probe: str         # mid-workflow scenario for testing
    # IDs of prior-run lessons that informed this proposal (for attribution).
    lessons_applied: list[LessonId] = Field(default_factory=list)


class FixValidation(BaseModel):
    issue_id: str
    applied: bool
    method: Literal["exact_anchor", "fuzzy_anchor", "llm_fallback", "failed"]
    assertion_passed: bool
    explanation: str
    # Approximate match ratio [0.0..1.0] when method is fuzzy_anchor; None otherwise.
    match_confidence: Optional[float] = None


VerdictCategory = Literal["improved", "inconclusive", "unchanged", "regressed"]


class VerificationResult(BaseModel):
    issue_id: str
    structural_pass: bool
    behavioral_pass: bool          # True only for verdict=="improved"
    improvement_score: int         # 1-10
    # 4-way judge categorization. "inconclusive" means both behaviors were
    # already correct — the probe didn't exercise the bug. Not a failure, just
    # a signal that the next iteration needs a sharper adversarial probe.
    verdict_category: VerdictCategory = "unchanged"
    explanation: str
    original_probe_response: str
    fixed_probe_response: str
    remaining_concerns: Optional[str] = None
    iteration: int = 0
    regressed: bool = False


class DetectionResult(BaseModel):
    issues: list[Issue]
    analysis_notes: str


class AnalysisResult(BaseModel):
    proposals: list[FixProposal]


# -- Internal models for LLM JSON parsing --

class AssertionCheck(BaseModel):
    passed: bool
    explanation: str


class JudgeRaw(BaseModel):
    verdict: VerdictCategory
    improvement_score: int
    explanation: str
    remaining_concerns: Optional[str] = None


# -- Knowledge-graph models (persistent cross-run memory) --

class Lesson(BaseModel):
    """A consolidated, human-readable rule derived from multiple prior runs.

    Carries tags so the lesson-selector can match it against the current
    run's domain / modality / active-principle ids / issue patterns without
    another LLM call.
    """
    id: LessonId                           # "LSN-001"
    text: str                              # the rule itself
    tags: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"
    support: int = 1                       # how many runs back this up
    last_seen: str = ""                    # ISO date of most recent supporting run


class Triple(BaseModel):
    """A (head, relation, tail) edge in the graph, with support counts.

    Triples are the episodic layer; Lessons are the semantic consolidation
    of recurring triple patterns.
    """
    head: str
    relation: str
    tail: str
    support: int = 1
    last_seen: str = ""


class RunRecord(BaseModel):
    """One past run's summary — kept in the KG's run log section."""
    run_id: str                            # e.g. "run-2026-04-15T14:22"
    prompt_file: str                       # source file name only (no path)
    prompt_hash: str                       # short sha of the prompt text
    domain: str
    modality: str
    total_issues: int
    improved: int
    unchanged: int
    inconclusive: int
    regressed: int
    notes: str = ""                        # free-text highlights worth remembering


class KnowledgeGraph(BaseModel):
    """In-memory view of `memory/knowledge_graph.md`."""
    lessons: list[Lesson] = Field(default_factory=list)
    triples: list[Triple] = Field(default_factory=list)
    runs: list[RunRecord] = Field(default_factory=list)


class KGUpdate(BaseModel):
    """Return shape of the Haiku consolidation pass."""
    lessons: list[Lesson]
    triples: list[Triple]
    new_lesson_ids: list[LessonId] = Field(default_factory=list)
    retired_lesson_ids: list[LessonId] = Field(default_factory=list)
