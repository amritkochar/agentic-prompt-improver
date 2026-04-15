#!/usr/bin/env python3
"""Voice Agent Prompt Quality Tool — CLI entry point.

Thin wrapper: parses flags, handles pre-flight, runs the six-pass pipeline
delegating orchestration to `pipeline.py`, reports via `reporting.py`, and
updates the persistent knowledge graph via `memory.py` + `agents.consolidate`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

import agents
from core import loader
from core import memory as kg_mem
from core import ui
from core.pipeline import build_summary, run_fix_verify_loop
from core.reporting import write_report

load_dotenv()

logger = logging.getLogger("prompt_improver")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="[%(levelname)s %(name)s] %(message)s",
        stream=sys.stderr,
    )


@click.command()
@click.argument("prompt_file", type=click.Path(exists=True))
@click.option("--auto-fix", is_flag=True, help="Apply all fixes without prompting.")
@click.option("--dry-run", is_flag=True, help="Detect issues only — no fixes or verification.")
@click.option("--output-dir", default="output", help="Output directory (default: output).")
@click.option(
    "--iterate", "max_iterations", type=int, default=1, show_default=True,
    help="Max analyze/apply/verify iterations. >1 re-runs failed fixes with verdict feedback.",
)
@click.option(
    "--multi-turn", "num_probe_turns", type=int, default=1, show_default=True,
    help="Number of caller turns per behavioral probe (1 = single-turn, default).",
)
@click.option(
    "--concurrency", type=int, default=5, show_default=True,
    help="Max concurrent LLM calls during Pass 4/5 verification. Set to 1 for sequential.",
)
@click.option(
    "--memory-file", default=str(kg_mem.DEFAULT_KG_PATH), show_default=True,
    help="Path to the cross-run knowledge-graph markdown file.",
)
@click.option(
    "--no-memory", is_flag=True,
    help="Do not read from or write to the knowledge graph this run.",
)
@click.option(
    "--no-memory-update", is_flag=True,
    help="Read the knowledge graph but do not update it after this run.",
)
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging (debug level).")
def main(
    prompt_file: str,
    auto_fix: bool,
    dry_run: bool,
    output_dir: str,
    max_iterations: int,
    num_probe_turns: int,
    concurrency: int,
    memory_file: str,
    no_memory: bool,
    no_memory_update: bool,
    verbose: bool,
):
    """Analyze a conversational agent prompt for quality issues and apply verified fixes."""

    _configure_logging(verbose)

    # ── Pre-flight ──────────────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        ui.show_error(
            "ANTHROPIC_API_KEY not set.\n"
            '  export ANTHROPIC_API_KEY="sk-ant-..."\n'
            "  or add it to a .env file in this directory."
        )
        sys.exit(1)

    if max_iterations < 1:
        ui.show_error("--iterate must be >= 1")
        sys.exit(1)
    if num_probe_turns < 1:
        ui.show_error("--multi-turn must be >= 1")
        sys.exit(1)
    if concurrency < 1:
        ui.show_error("--concurrency must be >= 1")
        sys.exit(1)
    agents.llm.set_concurrency(concurrency)

    # ── Load prompt ─────────────────────────────────────────────────────
    try:
        data = loader.load(prompt_file)
        prompt_text = loader.extract_prompt_text(data)
        agent_name = loader.get_agent_name(data)
    except Exception as e:
        ui.show_error(f"Failed to load prompt: {e}")
        sys.exit(1)

    tools_json = None
    for key in ("general_tools", "tools"):
        if key in data:
            tools_json = json.dumps(data[key], indent=2)
            break

    ui.show_header(agent_name, len(prompt_text))

    memory_path = Path(memory_file)

    # ── Pass 0: Establish Guiding Principles ────────────────────────────
    ui.show_step(0, "Establish Guiding Principles")
    ui.show_progress("Adapting canonical principles to this prompt")
    snap = agents.llm.snapshot()
    brief = agents.establish_principles(prompt_text, tools_json)
    ui.show_principles_summary(brief)
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 0")

    # ── Pass 0.5: Load cross-run lessons ────────────────────────────────
    ui.show_step(0, "Load Prior-Run Lessons")
    if no_memory:
        ui.console.print("  [dim]Memory disabled via --no-memory.[/dim]\n")
        kg = kg_mem.KnowledgeGraph()
        prelim_lessons = []
    else:
        kg = kg_mem.load_kg(memory_path)
        ui.show_progress(
            f"KG has {len(kg.lessons)} lesson(s) total, "
            f"{len(kg.triples)} triple(s)"
        )
        # Filter on brief alone for now; we refilter after detection so lessons
        # tagged with specific principle IDs only survive if the detection pass
        # actually flagged one of those principles.
        prelim_lessons = kg_mem.select_relevant_lessons(kg, brief, issues=[])
        ui.show_lessons_loaded(prelim_lessons, str(memory_path))

    # ── Pass 1: Detection + Reflection ──────────────────────────────────
    ui.show_step(1, "Detection + Reflection")
    ui.show_progress("Analyzing prompt for quality issues")
    snap = agents.llm.snapshot()
    detection = agents.detect(prompt_text, tools_json, brief=brief)
    ui.show_progress(f"Found {len(detection.issues)} issues after reflection")
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 1")

    # Re-filter lessons now that we know which issues (and therefore which
    # principles) this run actually touches.
    lessons = kg_mem.select_relevant_lessons(kg, brief, detection.issues)
    lessons_text = kg_mem.format_lessons_for_prompt(lessons)
    lessons_applied_ids = [l.id for l in lessons]
    if not no_memory and [l.id for l in lessons] != [l.id for l in prelim_lessons]:
        ui.console.print(
            "[dim]Refiltered lessons after detection (issue-specific tags now available):[/dim]"
        )
        ui.show_lessons_loaded(lessons, str(memory_path))

    if not detection.issues:
        ui.console.print("\n[green]No issues detected. Prompt looks clean![/green]")
        write_report(
            output_dir, detection, None, [], [], [], data, brief, prompt_text, prompt_text,
            memory_info=_mk_memory_info(lessons_applied_ids, no_memory, 0, 0),
        )
        _maybe_update_kg(
            no_memory, no_memory_update, kg, memory_path, prompt_file, prompt_text,
            brief, [], [], build_summary(detection, [], [], []),
        )
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    # ── Pass 2 (initial): Fix Analysis ──────────────────────────────────
    ui.show_step(2, "Fix Analysis")
    ui.show_progress("Generating fix proposals with assertions and behavioral probes")
    snap = agents.llm.snapshot()
    analysis = agents.analyze(
        prompt_text, detection.issues, brief=brief, lessons=lessons_text,
    )
    ui.show_progress(f"Generated {len(analysis.proposals)} fix proposals")
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 2")

    ui.show_issues_table(detection.issues, analysis.proposals)

    if dry_run:
        ui.console.print("[dim]Dry run — skipping fixes and verification.[/dim]")
        write_report(
            output_dir, detection, analysis, [], [], [], data, brief,
            prompt_text, prompt_text,
            memory_info=_mk_memory_info(lessons_applied_ids, no_memory, 0, 0),
        )
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    # ── User Selection ──────────────────────────────────────────────────
    if auto_fix:
        selected_ids = [issue.id for issue in detection.issues]
        ui.console.print(f"[dim]Auto-fix: selecting all {len(selected_ids)} issues.[/dim]")
    else:
        selected_ids = ui.get_user_selection(detection.issues)

    if not selected_ids:
        ui.console.print("[dim]No issues selected. Writing detection report only.[/dim]")
        write_report(
            output_dir, detection, analysis, [], [], [], data, brief,
            prompt_text, prompt_text,
            memory_info=_mk_memory_info(lessons_applied_ids, no_memory, 0, 0),
        )
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    ui.console.print(f"\n[bold]Selected {len(selected_ids)} issues for fixing.[/bold]")

    # ── Passes 3 + 4 (+ optional iteration) ─────────────────────────────
    issue_map = {i.id: i for i in detection.issues}
    fixed_json, fixed_text, all_validations, all_verdicts = run_fix_verify_loop(
        data=data,
        prompt_text=prompt_text,
        analysis=analysis,
        selected_ids=selected_ids,
        brief=brief,
        issue_map=issue_map,
        max_iterations=max_iterations,
        num_probe_turns=num_probe_turns,
        lessons_text=lessons_text,
    )

    summary = build_summary(detection, selected_ids, all_validations, all_verdicts)

    # ── Pass 6: Update the knowledge graph ──────────────────────────────
    kg_update_info = _maybe_update_kg(
        no_memory, no_memory_update, kg, memory_path, prompt_file, prompt_text,
        brief, all_validations, all_verdicts, summary,
    )

    write_report(
        output_dir,
        detection,
        analysis,
        selected_ids,
        all_validations,
        all_verdicts,
        fixed_json,
        brief,
        prompt_text,
        fixed_text,
        memory_info=_mk_memory_info(
            lessons_applied_ids, no_memory,
            kg_update_info.get("new_lessons", 0),
            kg_update_info.get("retired_lessons", 0),
        ),
    )
    ui.show_final_summary(
        all_verdicts, all_validations, output_dir,
        len(detection.issues), len(selected_ids),
    )
    ui.show_llm_stats_summary(agents.llm.snapshot())


# ── Helpers ─────────────────────────────────────────────────────────────────

def _mk_memory_info(
    lessons_applied: list[str], disabled: bool,
    new_lessons: int, retired_lessons: int,
) -> dict:
    return {
        "enabled": not disabled,
        "lessons_applied": lessons_applied,
        "new_lessons": new_lessons,
        "retired_lessons": retired_lessons,
    }


def _maybe_update_kg(
    no_memory: bool,
    no_memory_update: bool,
    kg,
    memory_path: Path,
    prompt_file: str,
    prompt_text: str,
    brief,
    validations,
    verdicts,
    summary: dict,
) -> dict:
    """Run the consolidation pass and persist to disk unless disabled."""
    if no_memory or no_memory_update:
        return {"new_lessons": 0, "retired_lessons": 0}

    try:
        ui.show_step(6, "Knowledge-Graph Consolidation")
        ui.show_progress(f"Updating {memory_path}")
        run_record = kg_mem.build_run_record(prompt_file, prompt_text, brief, summary)
        snap = agents.llm.snapshot()
        update = agents.consolidate(kg, run_record, validations, verdicts)
        ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 6")

        kg.lessons = update.lessons
        kg.triples = update.triples
        kg_mem.write_kg(kg, memory_path, append_run=run_record)
        ui.console.print(
            f"[dim]KG updated: {len(kg.lessons)} lesson(s), "
            f"{len(kg.triples)} triple(s). "
            f"+{len(update.new_lesson_ids)} new, -{len(update.retired_lesson_ids)} retired.[/dim]"
        )
        return {
            "new_lessons": len(update.new_lesson_ids),
            "retired_lessons": len(update.retired_lesson_ids),
        }
    except Exception as err:
        logger.warning("Knowledge-graph update failed: %s", err)
        ui.console.print(f"[yellow]KG update skipped: {err}[/yellow]")
        return {"new_lessons": 0, "retired_lessons": 0}


if __name__ == "__main__":
    main()
