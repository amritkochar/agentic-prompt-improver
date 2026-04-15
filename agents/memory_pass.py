"""Pass 6: Consolidate this run's outcomes into the persistent knowledge graph.

A cheap Haiku call takes the existing KG + the latest RunRecord + per-issue
validations/verdicts and returns an updated `(lessons, triples)` set. This is
where episodic observations become semantic rules (e.g. "replace_large_block
keeps collapsing to unchanged"). The caller (memory.py) handles file I/O.
"""

from __future__ import annotations

import json
import logging
from typing import Iterable

from models import FixValidation, KGUpdate, KnowledgeGraph, RunRecord, VerificationResult

from .llm import HAIKU_MODEL, llm
from .prompts import CONSOLIDATION_PROMPT

logger = logging.getLogger(__name__)


def consolidate(
    kg: KnowledgeGraph,
    run: RunRecord,
    validations: Iterable[FixValidation],
    verdicts: Iterable[VerificationResult],
) -> KGUpdate:
    """Ask Haiku to merge this run's evidence into the KG.

    On any failure (API error, parse error) returns a minimal no-op update
    that preserves the existing KG — we never want KG maintenance to kill
    the run or corrupt the file.
    """
    payload = {
        "current_kg": kg.model_dump(),
        "run": run.model_dump(),
        "validations": [v.model_dump() for v in validations],
        "verdicts": [v.model_dump() for v in verdicts],
    }

    try:
        return llm.call_json(
            CONSOLIDATION_PROMPT,
            json.dumps(payload, indent=2),
            KGUpdate,
            max_tokens=8192,
            model=HAIKU_MODEL,
        )
    except Exception as err:
        logger.warning("KG consolidation failed — preserving existing KG: %s", err)
        return KGUpdate(
            lessons=list(kg.lessons),
            triples=list(kg.triples),
            new_lesson_ids=[],
            retired_lesson_ids=[],
        )
