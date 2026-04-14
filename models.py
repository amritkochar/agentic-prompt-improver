"""Pydantic data models for all inter-pass data."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


Dimension = Literal["patient_experience", "workflow_adherence", "principles"]

Modality = Literal["voice", "chat", "sms", "mixed", "unknown"]


class ActivePrinciple(BaseModel):
    id: str           # "STYLE-01", "TOOL-04", etc.
    reason: str       # why this principle is load-bearing for THIS prompt


class PrinciplesBrief(BaseModel):
    """Pass 0 output: an adaptive quality lens for the specific input prompt."""

    modality: Modality
    domain_signals: list[str]                 # e.g. ["healthcare", "scheduling", "sms"]
    active_principles: list[ActivePrinciple]  # subset of canonical library
    interaction_contract: str                 # one-paragraph tailored contract
    structure_notes: str                      # cacheability / layout observations


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


class FixValidation(BaseModel):
    issue_id: str
    applied: bool
    method: Literal["exact_anchor", "fuzzy_anchor", "llm_fallback", "failed"]
    assertion_passed: bool
    explanation: str


class VerificationResult(BaseModel):
    issue_id: str
    structural_pass: bool
    behavioral_pass: bool
    improvement_score: int        # 1-10
    explanation: str
    original_probe_response: str
    fixed_probe_response: str
    remaining_concerns: Optional[str] = None


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
    improvement_detected: bool
    improvement_score: int
    explanation: str
    remaining_concerns: Optional[str] = None
