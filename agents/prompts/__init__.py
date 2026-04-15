"""Prompt templates for every LLM pass, organised by pass number.

Each sub-module owns one pass. This __init__ re-exports every constant so
existing callers (`from .prompts import DETECTION_PROMPT`) keep working
without changes.

Sub-modules:
  principles  — Pass 0: PRINCIPLES_INSTRUCTION
  detect      — Pass 1: DETECTION_PROMPT, REFLECTION_PROMPT
  analyze     — Pass 2: ANALYSIS_PROMPT
  fix         — Pass 3: LLM_FIX_PROMPT, ASSERTION_CHECK_PROMPT
  verify      — Pass 4: PROBE_PROMPT, JUDGE_PROMPT, FOLLOWUP_GENERATOR_PROMPT
  memory      — Pass 6: CONSOLIDATION_PROMPT
"""

from .principles import PRINCIPLES_INSTRUCTION
from .detect import DETECTION_PROMPT, REFLECTION_PROMPT
from .analyze import ANALYSIS_PROMPT
from .fix import LLM_FIX_PROMPT, ASSERTION_CHECK_PROMPT
from .verify import PROBE_PROMPT, JUDGE_PROMPT, FOLLOWUP_GENERATOR_PROMPT
from .memory import CONSOLIDATION_PROMPT

__all__ = [
    "PRINCIPLES_INSTRUCTION",
    "DETECTION_PROMPT",
    "REFLECTION_PROMPT",
    "ANALYSIS_PROMPT",
    "LLM_FIX_PROMPT",
    "ASSERTION_CHECK_PROMPT",
    "PROBE_PROMPT",
    "JUDGE_PROMPT",
    "FOLLOWUP_GENERATOR_PROMPT",
    "CONSOLIDATION_PROMPT",
]
