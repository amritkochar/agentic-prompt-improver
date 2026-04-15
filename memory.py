"""Persistent cross-run knowledge graph — read, filter, update, write.

Stored as a single markdown file (default `memory/knowledge_graph.md`) with
three sections: Lessons, Triples, and Run log. The file is committed to the
repo so the graph evolves visibly in version control. All LLM-touching
consolidation lives in `agents.memory_pass`; this module is pure I/O +
deterministic selection.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from models import (
    FixValidation,
    Issue,
    KnowledgeGraph,
    Lesson,
    PrinciplesBrief,
    RunRecord,
    Triple,
    VerificationResult,
)

logger = logging.getLogger(__name__)


DEFAULT_KG_PATH = Path("memory/knowledge_graph.md")

MAX_LESSONS = 30
MAX_TRIPLES = 150
MAX_RUNS_IN_LOG = 20
MAX_LESSONS_INJECTED = 10


# ── Load / parse ─────────────────────────────────────────────────────────────

_LESSON_LINE_RE = re.compile(
    r"^- \[(?P<id>LSN-\d+)\] (?P<text>.*?) "
    r"\|tags=(?P<tags>[^|]*)\|confidence=(?P<conf>[a-z]+)\|"
    r"support=(?P<support>\d+)\|last_seen=(?P<seen>[^|\n]*)$"
)

_TRIPLE_ROW_RE = re.compile(
    r"^\| (?P<head>[^|]+?) \| (?P<rel>[^|]+?) \| (?P<tail>[^|]+?) \| "
    r"(?P<support>\d+) \| (?P<seen>[^|]+?) \|$"
)


def load_kg(path: Optional[Path] = None) -> KnowledgeGraph:
    """Parse the markdown KG. Missing or malformed → empty KG (logged)."""
    path = path or DEFAULT_KG_PATH
    if not path.exists():
        return KnowledgeGraph()

    try:
        content = path.read_text()
    except Exception as err:
        logger.warning("Could not read KG at %s: %s", path, err)
        return KnowledgeGraph()

    lessons: list[Lesson] = []
    triples: list[Triple] = []
    runs: list[RunRecord] = []

    section = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Lessons"):
            section = "lessons"
            continue
        if stripped.startswith("## Triples"):
            section = "triples"
            continue
        if stripped.startswith("## Run log"):
            section = "runs"
            continue
        if not stripped or stripped.startswith("#"):
            continue

        if section == "lessons":
            m = _LESSON_LINE_RE.match(stripped)
            if m:
                tags = [t.strip() for t in m.group("tags").split(",") if t.strip()]
                conf = m.group("conf")
                if conf not in ("low", "medium", "high"):
                    conf = "medium"
                lessons.append(Lesson(
                    id=m.group("id"),
                    text=m.group("text"),
                    tags=tags,
                    confidence=conf,  # type: ignore[arg-type]
                    support=int(m.group("support")),
                    last_seen=m.group("seen"),
                ))
        elif section == "triples":
            if stripped.startswith("|---") or stripped.startswith("| Head"):
                continue
            m = _TRIPLE_ROW_RE.match(stripped)
            if m:
                triples.append(Triple(
                    head=m.group("head"),
                    relation=m.group("rel"),
                    tail=m.group("tail"),
                    support=int(m.group("support")),
                    last_seen=m.group("seen"),
                ))

    # Run log is free-text and not round-tripped; we preserve it by keeping
    # raw lines in a list we don't parse back — the consolidation pass can
    # recreate it from RunRecord inputs.
    return KnowledgeGraph(lessons=lessons, triples=triples, runs=runs)


# ── Selection ────────────────────────────────────────────────────────────────

def select_relevant_lessons(
    kg: KnowledgeGraph,
    brief: Optional[PrinciplesBrief],
    issues: list[Issue],
    max_out: int = MAX_LESSONS_INJECTED,
) -> list[Lesson]:
    """Pick lessons relevant to the current run via tag overlap, ranked by
    support × recency × confidence. Deterministic — no LLM call."""
    if not kg.lessons:
        return []

    run_tags: set[str] = set()
    if brief is not None:
        if brief.domain:
            run_tags.add(brief.domain.lower())
        if brief.modality:
            run_tags.add(brief.modality.lower())
        run_tags.update(s.lower() for s in brief.domain_signals)
        run_tags.update(p.id for p in brief.active_principles)
    for issue in issues:
        # Principle IDs embedded in issue IDs like PRIN-TOOL-04.
        if issue.id.startswith("PRIN-"):
            run_tags.add(issue.id.replace("PRIN-", ""))
        run_tags.add(issue.dimension)

    conf_weight = {"high": 3.0, "medium": 2.0, "low": 1.0}

    def score(lesson: Lesson) -> float:
        if not lesson.tags:
            overlap = 0.5  # untagged lessons get a small baseline, not zero
        else:
            overlap = sum(1 for t in lesson.tags if t.lower() in run_tags)
            if overlap == 0:
                return -1.0  # filter out
        return overlap * conf_weight.get(lesson.confidence, 1.0) * max(1, lesson.support)

    scored = [(score(l), l) for l in kg.lessons]
    scored = [s for s in scored if s[0] >= 0]
    scored.sort(key=lambda s: s[0], reverse=True)
    return [l for _, l in scored[:max_out]]


def format_lessons_for_prompt(lessons: list[Lesson]) -> str:
    """Render selected lessons as a `<prior_run_lessons>` block for analyze()."""
    if not lessons:
        return ""
    body = "\n".join(
        f"- [{l.id}] ({l.confidence}, support={l.support}) {l.text}"
        for l in lessons
    )
    return (
        "<prior_run_lessons>\n"
        "Consolidated lessons from previous runs of this tool. Let these "
        "steer your fix proposals — when a lesson applies, reference its "
        "ID in `lessons_applied`.\n\n"
        f"{body}\n"
        "</prior_run_lessons>"
    )


# ── Build run record ─────────────────────────────────────────────────────────

def build_run_record(
    prompt_file: str,
    prompt_text: str,
    brief: Optional[PrinciplesBrief],
    summary: dict,
    notes: str = "",
) -> RunRecord:
    """Package the current run's summary stats into a RunRecord."""
    return RunRecord(
        run_id="run-" + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"),
        prompt_file=Path(prompt_file).name,
        prompt_hash=hashlib.sha1(prompt_text.encode("utf-8")).hexdigest()[:10],
        domain=(brief.domain if brief else "unknown"),
        modality=(brief.modality if brief else "unknown"),
        total_issues=summary.get("total_issues", 0),
        improved=summary.get("improved_count", 0),
        unchanged=summary.get("unchanged_count", 0),
        inconclusive=summary.get("inconclusive_count", 0),
        regressed=summary.get("regressed_count", 0),
        notes=notes,
    )


# ── Write ────────────────────────────────────────────────────────────────────

def write_kg(
    kg: KnowledgeGraph,
    path: Optional[Path] = None,
    append_run: Optional[RunRecord] = None,
) -> None:
    """Serialise the KG back to markdown. Trims to configured caps."""
    path = path or DEFAULT_KG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    lessons = _trim_lessons(kg.lessons)
    triples = _trim_triples(kg.triples)
    runs = list(kg.runs)
    if append_run is not None:
        runs.append(append_run)
    runs = runs[-MAX_RUNS_IN_LOG:]

    lines: list[str] = [
        "# Agentic Prompt Improver — Knowledge Graph",
        "",
        "This file is maintained by the tool itself. Each run appends a log",
        "entry and the consolidation pass merges observations into the",
        "lessons + triples sections above. Feel free to read; edits will be",
        "merged on the next run.",
        "",
        "## Lessons (semantic layer, consolidated)",
        "",
    ]
    if not lessons:
        lines.append("_No lessons yet — this file will fill in as the tool runs._")
    else:
        for l in lessons:
            tags = ", ".join(l.tags)
            lines.append(
                f"- [{l.id}] {l.text} "
                f"|tags={tags}|confidence={l.confidence}|"
                f"support={l.support}|last_seen={l.last_seen}"
            )
    lines.extend(["", "## Triples (graph edges, with support counts)", ""])
    if not triples:
        lines.append("_No triples recorded yet._")
    else:
        lines.append("| Head | Relation | Tail | Support | Last seen |")
        lines.append("|---|---|---|---|---|")
        for t in triples:
            lines.append(
                f"| {t.head} | {t.relation} | {t.tail} | "
                f"{t.support} | {t.last_seen} |"
            )

    lines.extend(["", f"## Run log (episodic layer, last {MAX_RUNS_IN_LOG})", ""])
    if not runs:
        lines.append("_No runs recorded yet._")
    else:
        for r in runs:
            lines.append(
                f"### {r.run_id} — {r.prompt_file} "
                f"(hash={r.prompt_hash}, domain={r.domain}, modality={r.modality})"
            )
            lines.append(
                f"- issues={r.total_issues}, improved={r.improved}, "
                f"unchanged={r.unchanged}, inconclusive={r.inconclusive}, "
                f"regressed={r.regressed}"
            )
            if r.notes:
                lines.append(f"- notes: {r.notes}")
            lines.append("")

    path.write_text("\n".join(lines) + "\n")


def _trim_lessons(lessons: list[Lesson]) -> list[Lesson]:
    """Cap lesson list by keeping top-N by (confidence weight × support)."""
    conf_weight = {"high": 3, "medium": 2, "low": 1}
    ranked = sorted(
        lessons,
        key=lambda l: (conf_weight.get(l.confidence, 1) * l.support, l.last_seen),
        reverse=True,
    )
    return ranked[:MAX_LESSONS]


def _trim_triples(triples: list[Triple]) -> list[Triple]:
    ranked = sorted(triples, key=lambda t: (t.support, t.last_seen), reverse=True)
    return ranked[:MAX_TRIPLES]
