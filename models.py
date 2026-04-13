"""Pydantic data models for all inter-pass data."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class Issue(BaseModel):
    id: str                                      # "WA-01", "PE-03"
    dimension: Literal["patient_experience", "workflow_adherence"]
    severity: Literal["critical", "high", "medium", "low"]
    title: str
    description: str                             # What the problem is and why it matters
    evidence: str                                # Verbatim quote from prompt
    location_hint: str                           # Which section


class FixProposal(BaseModel):
    issue_id: str
    root_cause: str
    impact_if_unfixed: str
    fix_description: str
    original_text: str                           # Verbatim substring to replace
    replacement_text: str
    is_addition: bool = False
    insertion_after: Optional[str] = None


class ScenarioResult(BaseModel):
    scenario_description: str
    user_message: str
    why_adversarial: str


class JudgeRaw(BaseModel):
    improvement_detected: bool
    improvement_score: int                       # 1-10
    explanation: str
    remaining_concerns: Optional[str] = None


class JudgeVerdict(BaseModel):
    issue_id: str
    scenario_description: str
    user_message: str
    original_response: str
    fixed_response: str
    improvement_detected: bool
    improvement_score: int                       # 1-10
    explanation: str
    remaining_concerns: Optional[str] = None


class DetectionResult(BaseModel):
    issues: list[Issue]
    analysis_notes: str


class AnalysisResult(BaseModel):
    proposals: list[FixProposal]
