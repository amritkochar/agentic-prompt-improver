"""End-to-end pipeline orchestration: fix + verify loop, regression sweep, summary.

Kept separate from `main.py` so the Click CLI in `main.py` reads as a thin
entry point. Everything here is pure coordination — no LLM prompts, no I/O.
"""

from __future__ import annotations

import asyncio
import logging
import traceback

import agents
import ui

logger = logging.getLogger("prompt_improver.pipeline")


def run_fix_verify_loop(
    *,
    data,
    prompt_text,
    analysis,
    selected_ids,
    brief,
    issue_map,
    max_iterations,
    num_probe_turns,
    lessons_text="",
):
    """Sync entry point — delegates to `arun_fix_verify_loop` via asyncio.run."""
    return asyncio.run(arun_fix_verify_loop(
        data=data,
        prompt_text=prompt_text,
        analysis=analysis,
        selected_ids=selected_ids,
        brief=brief,
        issue_map=issue_map,
        max_iterations=max_iterations,
        num_probe_turns=num_probe_turns,
        lessons_text=lessons_text,
    ))


async def arun_fix_verify_loop(
    *,
    data,
    prompt_text,
    analysis,
    selected_ids,
    brief,
    issue_map,
    max_iterations,
    num_probe_turns,
    lessons_text="",
):
    """Apply fixes and verify them; optionally iterate on failures.

    Returns (final_fixed_json, final_fixed_text, all_validations, all_verdicts).

    - Each iteration's validations accumulate (latest per issue wins).
    - After the final iteration, previously-passing verdicts are re-verified
      against the final fixed text; drops are flagged `regressed=True`.
    - `lessons_text` is injected into every re-analysis call (iteration ≥ 2)
      so cross-run lessons keep steering retries.
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
        ui.show_pass_stats(
            agents.llm.delta(agents.llm.snapshot(), snap),
            f"Pass 3 ({label or 'single'})",
        )

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

        ui.show_step(4, f"Behavioral Probe Verification {label}".rstrip())
        ui.show_verification_header()
        snap = agents.llm.snapshot()

        probe_ids = [iid for iid in verifiable_ids if iid in proposal_map]
        if probe_ids:
            ui.show_progress(
                f"Probing {len(probe_ids)} issue(s) in parallel "
                f"(concurrency cap: {agents.llm._concurrency})"
            )

        async def _probe_one(iid):
            return iid, await agents.averify(
                iid,
                proposal_map[iid],
                prompt_text,
                current_fixed_text,
                brief=brief,
                num_turns=num_probe_turns,
            )

        tasks = [asyncio.create_task(_probe_one(iid)) for iid in probe_ids]
        for coro in asyncio.as_completed(tasks):
            try:
                iid, verdict = await coro
                verdict.iteration = iteration
                verdicts_by_id[iid] = verdict
                ui.show_verdict(verdict)
            except Exception as e:
                logger.exception("Verification failed")
                ui.console.print(
                    f"  [red]Verification failed — {e}[/red]\n"
                    f"  [dim]{traceback.format_exc().splitlines()[-1]}[/dim]"
                )

        ui.show_pass_stats(
            agents.llm.delta(agents.llm.snapshot(), snap),
            f"Pass 4 ({label or 'single'})",
        )

        if iteration + 1 >= max_iterations:
            break

        # Retry set = behavioral failures + structural failures.
        retry_ids = [
            iid for iid in verifiable_ids
            if iid in verdicts_by_id
            and not verdicts_by_id[iid].behavioral_pass
        ]
        retry_ids.extend(
            v.issue_id for v in new_validations
            if not v.assertion_passed
            and v.issue_id not in retry_ids
        )
        if not retry_ids:
            break

        ui.console.print(ui.panel(
            f"[yellow]Re-iterating on {len(retry_ids)} fix(es) that did not clearly improve behavior.[/yellow]"
        ))

        retry_issues = [issue_map[i] for i in retry_ids if i in issue_map]
        feedback = format_retry_feedback(retry_ids, verdicts_by_id, validations_by_id)
        try:
            new_analysis = agents.analyze(
                current_fixed_text,
                retry_issues,
                brief=brief,
                retry_feedback=feedback,
                lessons=lessons_text,
            )
        except Exception as e:
            logger.exception("Re-analysis failed during iteration %d", iteration + 1)
            ui.console.print(f"[red]Re-analysis failed: {e}[/red]")
            break

        for p in new_analysis.proposals:
            proposal_map[p.issue_id] = p
        current_proposals = list(proposal_map.values())
        issues_in_flight = [p.issue_id for p in new_analysis.proposals]
        logger.info("Iteration %d feedback:\n%s", iteration + 1, feedback)

    if len(verdicts_by_id) > 1:
        await aregression_sweep(
            verdicts_by_id, proposal_map, prompt_text, current_fixed_text, brief,
            num_probe_turns=num_probe_turns,
        )

    return (
        current_fixed_json,
        current_fixed_text,
        list(validations_by_id.values()),
        list(verdicts_by_id.values()),
    )


def format_retry_feedback(retry_ids, verdicts_by_id, validations_by_id) -> str:
    """Structured feedback so the re-analysis LLM sees exactly what was wrong."""
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


async def aregression_sweep(
    verdicts_by_id,
    proposal_map,
    original_prompt,
    final_fixed_text,
    brief,
    num_probe_turns,
):
    """Re-verify previously-passing fixes against the final prompt text, in parallel.

    Catches regressions caused by overlapping edits across iterations.
    """
    ui.show_step(5, "Regression Check")
    ui.show_progress("Re-verifying previously-passing fixes against final prompt")
    regressed_count = 0
    snap = agents.llm.snapshot()

    targets = [
        (iid, prior, proposal_map.get(iid))
        for iid, prior in list(verdicts_by_id.items())
        if prior.behavioral_pass
        and proposal_map.get(iid) is not None
        and proposal_map.get(iid).dimension != "principles"
    ]

    async def _reverify(iid, prior, proposal):
        try:
            fresh = await agents.averify(
                iid, proposal, original_prompt, final_fixed_text,
                brief=brief, num_turns=num_probe_turns,
            )
        except Exception as e:
            logger.warning("Regression re-verify failed for %s: %s", iid, e)
            return iid, prior, None
        return iid, prior, fresh

    tasks = [asyncio.create_task(_reverify(iid, prior, p)) for iid, prior, p in targets]
    for coro in asyncio.as_completed(tasks):
        iid, prior, fresh = await coro
        if fresh is None:
            continue
        if not fresh.behavioral_pass and prior.behavioral_pass:
            fresh.regressed = True
            fresh.iteration = prior.iteration
            verdicts_by_id[iid] = fresh
            regressed_count += 1
            ui.console.print(
                f"  [red]⚠ {iid} regressed: score dropped "
                f"{prior.improvement_score} → {fresh.improvement_score}[/red]"
            )

    ui.show_pass_stats(agents.llm.delta(agents.llm.snapshot(), snap), "Pass 5")
    if regressed_count == 0:
        ui.console.print("  [green]No regressions detected.[/green]")


def regression_sweep(*args, **kwargs):
    """Sync shim for the async regression sweep."""
    return asyncio.run(aregression_sweep(*args, **kwargs))


def build_summary(detection, selected_ids, validations, verdicts) -> dict:
    """Aggregate per-verdict counters for the report."""
    verdicts = verdicts or []
    validations = validations or []

    def _cat(v) -> str:
        return getattr(v, "verdict_category", "unchanged")

    improved = sum(
        1 for v in verdicts
        if _cat(v) == "improved" and not getattr(v, "regressed", False)
    )
    inconclusive = sum(1 for v in verdicts if _cat(v) == "inconclusive")
    unchanged = sum(
        1 for v in verdicts
        if _cat(v) == "unchanged" and not getattr(v, "regressed", False)
    )
    regressed = sum(
        1 for v in verdicts
        if getattr(v, "regressed", False) or _cat(v) == "regressed"
    )

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
