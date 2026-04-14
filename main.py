#!/usr/bin/env python3
"""Voice Agent Prompt Quality Tool — CLI entry point and pipeline orchestration."""

import json
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

import agents
import loader
import ui

load_dotenv()


@click.command()
@click.argument("prompt_file", type=click.Path(exists=True))
@click.option("--auto-fix", is_flag=True, help="Apply all fixes without prompting.")
@click.option("--dry-run", is_flag=True, help="Detect issues only — no fixes or verification.")
@click.option("--output-dir", default="output", help="Output directory (default: output).")
def main(prompt_file: str, auto_fix: bool, dry_run: bool, output_dir: str):
    """Analyze a voice agent prompt for quality issues and apply verified fixes."""

    # ── Pre-flight ──────────────────────────────────────────────────────
    if not os.environ.get("ANTHROPIC_API_KEY"):
        ui.show_error(
            "ANTHROPIC_API_KEY not set.\n"
            '  export ANTHROPIC_API_KEY="sk-ant-..."\n'
            "  or add it to a .env file in this directory."
        )
        sys.exit(1)

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

    # ── Pass 0: Establish Guiding Principles ────────────────────────────
    ui.show_step(0, "Establish Guiding Principles")
    ui.show_progress("Adapting canonical principles to this prompt")
    snap = agents.llm.snapshot()
    brief = agents.establish_principles(prompt_text, tools_json)
    ui.show_principles_summary(brief)
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 0")

    # ── Pass 1: Detection + Reflection ──────────────────────────────────
    ui.show_step(1, "Detection + Reflection")
    ui.show_progress("Analyzing prompt for quality issues")
    snap = agents.llm.snapshot()
    detection = agents.detect(prompt_text, tools_json, brief=brief)
    ui.show_progress(f"Found {len(detection.issues)} issues after reflection")
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 1")

    if not detection.issues:
        ui.console.print("\n[green]No issues detected. Prompt looks clean![/green]")
        _write_report(output_dir, detection, None, [], [], [], data, brief)
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    # ── Pass 2: Fix Analysis ────────────────────────────────────────────
    ui.show_step(2, "Fix Analysis")
    ui.show_progress("Generating fix proposals with assertions and behavioral probes")
    snap = agents.llm.snapshot()
    analysis = agents.analyze(prompt_text, detection.issues, brief=brief)
    ui.show_progress(f"Generated {len(analysis.proposals)} fix proposals")
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 2")

    # Show the issues table
    ui.show_issues_table(detection.issues, analysis.proposals)

    if dry_run:
        ui.console.print("[dim]Dry run — skipping fixes and verification.[/dim]")
        _write_report(output_dir, detection, analysis, [], [], [], data, brief)
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    # ── User Selection (Human-in-the-Loop) ──────────────────────────────
    if auto_fix:
        selected_ids = [issue.id for issue in detection.issues]
        ui.console.print(f"[dim]Auto-fix: selecting all {len(selected_ids)} issues.[/dim]")
    else:
        selected_ids = ui.get_user_selection(detection.issues)

    if not selected_ids:
        ui.console.print("[dim]No issues selected. Writing detection report only.[/dim]")
        _write_report(output_dir, detection, analysis, [], [], [], data, brief)
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    ui.console.print(f"\n[bold]Selected {len(selected_ids)} issues for fixing.[/bold]")

    # ── Pass 3: Applying Fixes + Validation ─────────────────────────────
    ui.show_step(3, "Applying Fixes + Validation")
    snap = agents.llm.snapshot()
    fixed_json, fixed_text, applied_ids, validations = agents.apply_fixes(
        data, prompt_text, analysis.proposals, selected_ids
    )

    # Show per-fix application and validation status
    for v in validations:
        ui.show_fix_validation(v)
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 3")

    # Validation gate: only verify fixes that were applied and passed assertion
    verifiable_ids = [
        v.issue_id for v in validations
        if v.applied and v.assertion_passed
    ]
    skipped = len(applied_ids) - len(verifiable_ids)
    if skipped > 0:
        ui.console.print(
            f"\n[yellow]{skipped} fix(es) failed validation — skipping verification.[/yellow]"
        )
    ui.console.print(
        f"[bold]{len(verifiable_ids)} fix(es) passed validation — proceeding to verification.[/bold]"
    )

    # ── Pass 4: Behavioral Probe Verification ───────────────────────────
    ui.show_step(4, "Behavioral Probe Verification")
    ui.show_verification_header()

    proposal_map = {p.issue_id: p for p in analysis.proposals}
    verdicts = []
    snap = agents.llm.snapshot()

    for issue_id in verifiable_ids:
        if issue_id not in proposal_map:
            continue
        ui.show_progress(f"Probing {issue_id}")
        try:
            verdict = agents.verify(
                issue_id,
                proposal_map[issue_id],
                prompt_text,
                fixed_text,
                brief=brief,
            )
            verdicts.append(verdict)
            ui.show_verdict(verdict)
        except Exception as e:
            ui.console.print(f"  [red]{issue_id}: Verification failed — {e}[/red]")

    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 4")

    # ── Output ──────────────────────────────────────────────────────────
    _write_report(
        output_dir, detection, analysis, selected_ids, validations, verdicts, fixed_json, brief
    )
    ui.show_final_summary(
        verdicts, validations, output_dir, len(detection.issues), len(selected_ids)
    )
    ui.show_llm_stats_summary(agents.llm.snapshot())


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(output_dir, detection, analysis, selected_ids, validations, verdicts, data, brief=None):
    """Write report.json and fixed_prompt.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "principles_brief": brief.model_dump() if brief else None,
        "llm_stats": agents.llm.snapshot(),
        "detection": detection.model_dump() if detection else None,
        "analysis": analysis.model_dump() if analysis else None,
        "selected_issues": selected_ids,
        "validations": [v.model_dump() for v in validations] if validations else [],
        "verdicts": [v.model_dump() for v in verdicts] if verdicts else [],
        "summary": {
            "total_issues": len(detection.issues) if detection else 0,
            "selected_count": len(selected_ids),
            "applied_count": sum(1 for v in validations if v.applied) if validations else 0,
            "validated_count": (
                sum(1 for v in validations if v.applied and v.assertion_passed)
                if validations else 0
            ),
            "verified_count": len(verdicts),
            "improved_count": (
                sum(1 for v in verdicts if v.behavioral_pass) if verdicts else 0
            ),
            "avg_score": (
                round(sum(v.improvement_score for v in verdicts) / len(verdicts), 1)
                if verdicts
                else 0
            ),
        },
    }

    (out / "report.json").write_text(json.dumps(report, indent=2))

    if selected_ids and data:
        (out / "fixed_prompt.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    ui.console.print(f"\n[dim]Results written to {output_dir}/[/dim]")


if __name__ == "__main__":
    main()
