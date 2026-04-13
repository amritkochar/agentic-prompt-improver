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

    # ── Pass 1: Detection + Reflection ──────────────────────────────────
    ui.show_step(1, "Detection + Reflection")
    ui.show_progress("Analyzing prompt for quality issues")
    detection = agents.detect(prompt_text, tools_json)
    ui.show_progress(f"Found {len(detection.issues)} issues after reflection")

    if not detection.issues:
        ui.console.print("\n[green]No issues detected. Prompt looks clean![/green]")
        _write_report(output_dir, detection, None, [], [], data)
        return

    # ── Pass 2: Fix Analysis ────────────────────────────────────────────
    ui.show_step(2, "Fix Analysis")
    ui.show_progress("Generating fix proposals")
    analysis = agents.analyze(prompt_text, detection.issues)
    ui.show_progress(f"Generated {len(analysis.proposals)} fix proposals")

    # Show the issues table
    ui.show_issues_table(detection.issues, analysis.proposals)

    if dry_run:
        ui.console.print("[dim]Dry run — skipping fixes and verification.[/dim]")
        _write_report(output_dir, detection, analysis, [], [], data)
        return

    # ── User Selection (Human-in-the-Loop) ──────────────────────────────
    if auto_fix:
        selected_ids = [issue.id for issue in detection.issues]
        ui.console.print(f"[dim]Auto-fix: selecting all {len(selected_ids)} issues.[/dim]")
    else:
        selected_ids = ui.get_user_selection(detection.issues)

    if not selected_ids:
        ui.console.print("[dim]No issues selected. Writing detection report only.[/dim]")
        _write_report(output_dir, detection, analysis, [], [], data)
        return

    ui.console.print(f"\n[bold]Selected {len(selected_ids)} issues for fixing.[/bold]")

    # ── Pass 3: Applying Fixes ──────────────────────────────────────────
    ui.show_step(3, "Applying Fixes")
    fixed_json, fixed_text, applied_ids = agents.apply_fixes(
        data, prompt_text, analysis.proposals, selected_ids
    )

    for sid in selected_ids:
        status = "applied" if sid in applied_ids else "skipped (no match)"
        ui.show_fix_progress(sid, status)

    # ── Pass 4: Adversarial Verification ────────────────────────────────
    ui.show_step(4, "Adversarial Verification")
    ui.show_verification_header()

    proposal_map = {p.issue_id: p for p in analysis.proposals}
    verdicts = []

    for issue_id in applied_ids:
        if issue_id not in proposal_map:
            continue
        ui.show_progress(f"Verifying {issue_id}")
        try:
            verdict = agents.verify(
                issue_id, proposal_map[issue_id], prompt_text, fixed_text
            )
            verdicts.append(verdict)
            ui.show_verdict(verdict)
        except Exception as e:
            ui.console.print(f"  [red]{issue_id}: Verification failed — {e}[/red]")

    # ── Output ──────────────────────────────────────────────────────────
    _write_report(output_dir, detection, analysis, selected_ids, verdicts, fixed_json)
    ui.show_final_summary(verdicts, output_dir, len(detection.issues), len(selected_ids))


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(output_dir, detection, analysis, selected_ids, verdicts, data):
    """Write report.json and fixed_prompt.json."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "detection": detection.model_dump() if detection else None,
        "analysis": analysis.model_dump() if analysis else None,
        "selected_issues": selected_ids,
        "verdicts": [v.model_dump() for v in verdicts] if verdicts else [],
        "summary": {
            "total_issues": len(detection.issues) if detection else 0,
            "selected_count": len(selected_ids),
            "verified_count": len(verdicts),
            "improved_count": (
                sum(1 for v in verdicts if v.improvement_detected) if verdicts else 0
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
