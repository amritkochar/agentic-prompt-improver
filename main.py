#!/usr/bin/env python3
"""Voice Agent Prompt Quality Tool — CLI entry point and pipeline orchestration."""

import difflib
import json
import logging
import os
import sys
import traceback
from pathlib import Path

import click
from dotenv import load_dotenv

import agents
import loader
import ui

load_dotenv()

logger = logging.getLogger("prompt_improver")


def _configure_logging(verbose: bool) -> None:
    """Route warnings from agents.py and friends to stderr at a useful level."""
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
@click.option("-v", "--verbose", is_flag=True, help="Verbose logging (debug level).")
def main(
    prompt_file: str,
    auto_fix: bool,
    dry_run: bool,
    output_dir: str,
    max_iterations: int,
    num_probe_turns: int,
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
        _write_report(
            output_dir, detection, None, [], [], [], data, brief, prompt_text, prompt_text
        )
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    # ── Pass 2 (initial): Fix Analysis ──────────────────────────────────
    ui.show_step(2, "Fix Analysis")
    ui.show_progress("Generating fix proposals with assertions and behavioral probes")
    snap = agents.llm.snapshot()
    analysis = agents.analyze(prompt_text, detection.issues, brief=brief)
    ui.show_progress(f"Generated {len(analysis.proposals)} fix proposals")
    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 2")

    ui.show_issues_table(detection.issues, analysis.proposals)

    if dry_run:
        ui.console.print("[dim]Dry run — skipping fixes and verification.[/dim]")
        _write_report(
            output_dir, detection, analysis, [], [], [], data, brief, prompt_text, prompt_text
        )
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
        _write_report(
            output_dir, detection, analysis, [], [], [], data, brief, prompt_text, prompt_text
        )
        ui.show_llm_stats_summary(agents.llm.snapshot())
        return

    ui.console.print(f"\n[bold]Selected {len(selected_ids)} issues for fixing.[/bold]")

    # ── Passes 3+4 (with optional iteration) ────────────────────────────
    issue_map = {i.id: i for i in detection.issues}
    fixed_json, fixed_text, all_validations, all_verdicts = _run_fix_verify_loop(
        data=data,
        prompt_text=prompt_text,
        analysis=analysis,
        selected_ids=selected_ids,
        brief=brief,
        issue_map=issue_map,
        max_iterations=max_iterations,
        num_probe_turns=num_probe_turns,
    )

    # ── Output ──────────────────────────────────────────────────────────
    _write_report(
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
    )
    ui.show_final_summary(
        all_verdicts, all_validations, output_dir, len(detection.issues), len(selected_ids)
    )
    ui.show_llm_stats_summary(agents.llm.snapshot())


# ---------------------------------------------------------------------------
# Fix + verify loop (iteration + regression)
# ---------------------------------------------------------------------------

def _run_fix_verify_loop(
    *,
    data,
    prompt_text,
    analysis,
    selected_ids,
    brief,
    issue_map,
    max_iterations,
    num_probe_turns,
):
    """Apply fixes and verify them; optionally iterate on failures.

    Returns (final_fixed_json, final_fixed_text, all_validations, all_verdicts).

    - Each iteration's validations are accumulated (latest per issue wins).
    - After the final iteration, all previously-passing verdicts are
      re-verified against the final fixed_text and any that now fail are
      marked `regressed=True`.
    """
    current_proposals = list(analysis.proposals)
    current_fixed_json = dict(data)
    current_fixed_text = prompt_text
    validations_by_id: dict[str, object] = {}
    verdicts_by_id: dict[str, object] = {}
    issues_in_flight = list(selected_ids)

    proposal_map = {p.issue_id: p for p in current_proposals}

    for iteration in range(max_iterations):
        if not issues_in_flight:
            break

        label = f"Iteration {iteration + 1}/{max_iterations}" if max_iterations > 1 else ""
        ui.show_step(3, f"Applying Fixes + Validation {label}".rstrip())
        snap = agents.llm.snapshot()
        new_fixed_json, new_fixed_text, _applied_ids, new_validations = agents.apply_fixes(
            current_fixed_json, current_fixed_text, current_proposals, issues_in_flight
        )

        for v in new_validations:
            ui.show_fix_validation(v)
            validations_by_id[v.issue_id] = v
        ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), f"Pass 3 ({label or 'single'})")

        current_fixed_json = new_fixed_json
        current_fixed_text = new_fixed_text

        verifiable_ids = [
            v.issue_id for v in new_validations
            if v.applied and v.assertion_passed
        ]
        skipped = len(issues_in_flight) - len(verifiable_ids)
        if skipped > 0:
            ui.console.print(
                f"\n[yellow]{skipped} fix(es) failed validation — skipping verification.[/yellow]"
            )
        ui.console.print(
            f"[bold]{len(verifiable_ids)} fix(es) ready for behavioral verification.[/bold]"
        )

        # ── Pass 4: Verify ──
        ui.show_step(4, f"Behavioral Probe Verification {label}".rstrip())
        ui.show_verification_header()
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
                    current_fixed_text,
                    brief=brief,
                    num_turns=num_probe_turns,
                )
                verdict.iteration = iteration
                verdicts_by_id[issue_id] = verdict
                ui.show_verdict(verdict)
            except Exception as e:
                logger.exception("Verification failed for %s", issue_id)
                ui.console.print(
                    f"  [red]{issue_id}: Verification failed — {e}[/red]\n"
                    f"  [dim]{traceback.format_exc().splitlines()[-1]}[/dim]"
                )

        ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), f"Pass 4 ({label or 'single'})")

        # Decide which issues warrant another iteration.
        if iteration + 1 >= max_iterations:
            break

        # Retry when the verdict did not clearly pass. Threshold matches the
        # pass bar (score < 7) — anything below is either partial, unchanged,
        # inconclusive, or regressed. Inconclusive verdicts retry with a
        # sharper adversarial probe; partial/unchanged retry with a different
        # fix. This keeps the loop general rather than overfitting to a
        # specific score band.
        retry_ids = [
            iid for iid in verifiable_ids
            if iid in verdicts_by_id
            and not verdicts_by_id[iid].behavioral_pass
        ]
        # Also retry fixes that failed structural validation this round.
        retry_ids.extend(
            v.issue_id for v in new_validations
            if not v.assertion_passed
            and v.issue_id not in retry_ids
        )
        if not retry_ids:
            break

        ui.console.print(Panel_if_available(
            f"[yellow]Re-iterating on {len(retry_ids)} fix(es) that did not clearly improve behavior.[/yellow]"
        ))

        # Re-analyze only the retry set, piping the verdict feedback INTO the
        # analysis LLM (not just the logs) so it can propose a different fix.
        retry_issues = [issue_map[i] for i in retry_ids if i in issue_map]
        feedback = _format_retry_feedback(retry_ids, verdicts_by_id, validations_by_id)
        retry_prompt = current_fixed_text  # re-analyze against the latest text
        try:
            new_analysis = agents.analyze(
                retry_prompt, retry_issues, brief=brief, retry_feedback=feedback,
            )
        except Exception as e:
            logger.exception("Re-analysis failed during iteration %d", iteration + 1)
            ui.console.print(f"[red]Re-analysis failed: {e}[/red]")
            break

        # Merge: replace proposals for retry issues with fresh ones;
        # tag them with the feedback so the next apply round can log it.
        for p in new_analysis.proposals:
            proposal_map[p.issue_id] = p
        current_proposals = list(proposal_map.values())
        issues_in_flight = [p.issue_id for p in new_analysis.proposals]
        logger.info(
            "Iteration %d feedback:\n%s", iteration + 1, feedback,
        )

    # ── Regression sweep: re-verify previously-passing fixes against final text ──
    if len(verdicts_by_id) > 1:
        _regression_sweep(
            verdicts_by_id, proposal_map, prompt_text, current_fixed_text, brief,
            num_probe_turns=num_probe_turns,
        )

    return (
        current_fixed_json,
        current_fixed_text,
        list(validations_by_id.values()),
        list(verdicts_by_id.values()),
    )


def _format_retry_feedback(retry_ids, verdicts_by_id, validations_by_id) -> str:
    """Structured feedback on why each retry issue is back in the queue.

    Includes the judge's verdict category and remaining_concerns so the
    re-analysis LLM sees exactly what was wrong (and, for inconclusive
    verdicts, knows to design a sharper adversarial probe rather than
    regenerating the same fix).
    """
    lines = []
    for iid in retry_ids:
        v = verdicts_by_id.get(iid)
        val = validations_by_id.get(iid)
        if v is not None:
            category = getattr(v, "verdict_category", "unchanged")
            guidance = {
                "inconclusive": (
                    "The probe scenario did NOT trigger the bug — both the "
                    "original and fixed prompts produced correct behavior. "
                    "Keep the fix if it's still sound, but redesign the "
                    "behavioral_probe with an adversarial distractor that "
                    "forces the original's failure mode to surface."
                ),
                "unchanged": (
                    "The fix did not alter the problematic behavior. Propose "
                    "a DIFFERENT fix — change the anchor, rewrite new_content "
                    "to address the remaining concerns, or target a different "
                    "location in the prompt."
                ),
                "regressed": (
                    "The fix introduced a NEW problem. Propose a narrower "
                    "edit that resolves the original issue without the side "
                    "effect called out below."
                ),
            }.get(category, "Improve the fix based on the judge's notes below.")
            remaining = v.remaining_concerns or "(none provided)"
            lines.append(
                f"- {iid} [verdict={category}, score={v.improvement_score}/10]\n"
                f"  what happened: {v.explanation[:240]}\n"
                f"  remaining concerns: {remaining[:240]}\n"
                f"  guidance: {guidance}"
            )
        elif val is not None:
            lines.append(
                f"- {iid} [assertion_failed]\n"
                f"  reason: {val.explanation[:240]}\n"
                f"  guidance: The fix text did not land where expected or the "
                f"assertion was too vague. Choose a more unique anchor and a "
                f"concrete, verifiable assertion."
            )
    return "\n".join(lines)


def _regression_sweep(
    verdicts_by_id,
    proposal_map,
    original_prompt,
    final_fixed_text,
    brief,
    num_probe_turns,
):
    """Re-verify previously-passing fixes against the final prompt text.

    If an earlier fix no longer passes (score dropped below threshold) we mark
    it regressed=True in-place. This is cheap insurance when --iterate > 1 or
    when multiple fixes touch overlapping areas.
    """
    ui.show_step(5, "Regression Check")
    ui.show_progress("Re-verifying previously-passing fixes against final prompt")
    regressed_count = 0
    snap = agents.llm.snapshot()

    for issue_id, prior in list(verdicts_by_id.items()):
        # Only regression-check fixes that previously cleared the bar.
        if not prior.behavioral_pass:
            continue
        proposal = proposal_map.get(issue_id)
        if proposal is None or proposal.dimension == "principles":
            continue
        try:
            fresh = agents.verify(
                issue_id, proposal, original_prompt, final_fixed_text,
                brief=brief, num_turns=num_probe_turns,
            )
        except Exception as e:
            logger.warning("Regression re-verify failed for %s: %s", issue_id, e)
            continue

        if not fresh.behavioral_pass and prior.behavioral_pass:
            fresh.regressed = True
            fresh.iteration = prior.iteration  # preserve origin
            verdicts_by_id[issue_id] = fresh
            regressed_count += 1
            ui.console.print(
                f"  [red]⚠ {issue_id} regressed: score dropped "
                f"{prior.improvement_score} → {fresh.improvement_score}[/red]"
            )

    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 5")
    if regressed_count == 0:
        ui.console.print("  [green]No regressions detected.[/green]")


def _build_summary(detection, selected_ids, validations, verdicts) -> dict:
    """Aggregate per-verdict counters for the report.

    Inconclusive verdicts are tracked separately — they are *not* failures
    (the fix may be correct; the probe just didn't trigger the bug) and are
    excluded from the average score so the number reflects measurable change.
    """
    verdicts = verdicts or []
    validations = validations or []

    def _cat(v) -> str:
        return getattr(v, "verdict_category", "unchanged")

    improved = sum(1 for v in verdicts if _cat(v) == "improved" and not getattr(v, "regressed", False))
    inconclusive = sum(1 for v in verdicts if _cat(v) == "inconclusive")
    unchanged = sum(1 for v in verdicts if _cat(v) == "unchanged" and not getattr(v, "regressed", False))
    regressed = sum(1 for v in verdicts if getattr(v, "regressed", False) or _cat(v) == "regressed")

    scorable = [v for v in verdicts if _cat(v) != "inconclusive"]
    avg_score = (
        round(sum(v.improvement_score for v in scorable) / len(scorable), 1)
        if scorable else 0
    )

    return {
        "total_issues": len(detection.issues) if detection else 0,
        "selected_count": len(selected_ids),
        "applied_count": sum(1 for v in validations if v.applied),
        "validated_count": sum(1 for v in validations if v.applied and v.assertion_passed),
        "verified_count": len(verdicts),
        "improved_count": improved,
        "inconclusive_count": inconclusive,
        "unchanged_count": unchanged,
        "regressed_count": regressed,
        "avg_score_excluding_inconclusive": avg_score,
    }


def Panel_if_available(text: str):
    """Thin shim so we don't force a rich.Panel import in main.py flow."""
    from rich.panel import Panel
    return Panel(text, border_style="yellow")


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(
    output_dir, detection, analysis, selected_ids, validations, verdicts, data, brief,
    original_prompt_text, final_prompt_text,
):
    """Write report.json, fixed_prompt.json, and prompt.diff."""
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
        "summary": _build_summary(detection, selected_ids, validations, verdicts),
    }

    (out / "report.json").write_text(json.dumps(report, indent=2))

    if selected_ids and data:
        (out / "fixed_prompt.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Unified diff between original and final prompt text. Written even when
    # the text is unchanged so readers have a predictable artifact set.
    diff_lines = difflib.unified_diff(
        original_prompt_text.splitlines(keepends=True),
        final_prompt_text.splitlines(keepends=True),
        fromfile="original_prompt.txt",
        tofile="fixed_prompt.txt",
        n=3,
    )
    diff_text = "".join(diff_lines) or "(no changes)\n"
    (out / "prompt.diff").write_text(diff_text)

    ui.console.print(f"\n[dim]Results written to {output_dir}/[/dim]")


if __name__ == "__main__":
    main()
