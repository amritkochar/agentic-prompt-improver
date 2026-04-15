"""Rich terminal rendering + interactive selection."""

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from models import FixProposal, FixValidation, Issue, PrinciplesBrief, VerificationResult

console = Console()

SEVERITY_COLORS = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
}

DIMENSION_LABELS = {
    "workflow_adherence": "Workflow",
    "patient_experience": "Patient Exp",
    "principles": "Principles",
}

METHOD_LABELS = {
    "exact_anchor": "[green]exact[/green]",
    "fuzzy_anchor": "[yellow]fuzzy[/yellow]",
    "llm_fallback": "[yellow]llm fallback[/yellow]",
    "failed": "[red]failed[/red]",
}


# ---------------------------------------------------------------------------
# Header / progress
# ---------------------------------------------------------------------------

def show_header(agent_name: str, prompt_length: int):
    console.print()
    console.print(Panel(
        f"[bold]Voice Agent Prompt Quality Tool[/bold]\n"
        f"Agent: [cyan]{agent_name}[/cyan]  |  Prompt: {prompt_length:,} chars",
        style="blue",
        box=box.DOUBLE,
    ))
    console.print()


def show_step(step: int, title: str):
    console.print()
    console.print(f"[bold blue]{'━' * 3} Pass {step}: {title} {'━' * 40}[/bold blue]")
    console.print()


def show_progress(message: str):
    console.print(f"  [dim]{message}...[/dim]")


HEALTHCARE_DOMAIN_TAGS = {"healthcare", "clinical", "phi"}


def show_principles_summary(brief: PrinciplesBrief):
    """Print a compact one-line summary of the principles brief plus the contract."""
    ids = ", ".join(p.id for p in brief.active_principles)
    signals = ", ".join(brief.domain_signals) if brief.domain_signals else "(none)"

    console.print(
        f"  [bold]Modality:[/bold] [cyan]{brief.modality}[/cyan]  |  "
        f"[bold]Domain:[/bold] [cyan]{brief.domain}[/cyan]  |  "
        f"[bold]Signals:[/bold] {signals}  |  "
        f"[bold]{len(brief.active_principles)} principles active[/bold]"
    )
    if ids:
        console.print(f"  [dim]{ids}[/dim]")
    console.print(Panel(
        brief.interaction_contract,
        title="[bold]Interaction Contract[/bold]",
        border_style="dim",
    ))
    if brief.structure_notes:
        console.print(f"  [dim]Structure: {brief.structure_notes}[/dim]")

    # Domain gate: this tool's canonical principles library was tuned on
    # healthcare voice agents. Warn when the inferred domain is something
    # else so the user knows detection rules may need manual calibration.
    inferred = (brief.domain or "").lower()
    signal_tags = {s.lower() for s in brief.domain_signals}
    is_healthcare = (
        inferred in HEALTHCARE_DOMAIN_TAGS
        or bool(signal_tags & HEALTHCARE_DOMAIN_TAGS)
    )
    if not is_healthcare and inferred != "unknown":
        console.print(Panel(
            f"Inferred domain is [bold]{brief.domain}[/bold], not healthcare. "
            "The canonical principles library is healthcare-tuned; general "
            "quality checks still apply, but domain-specific rules (eligibility, "
            "PHI, 5-Ws notifications) will not fire. Verify detection results "
            "manually before acting on them.",
            title="[yellow]Non-healthcare domain detected[/yellow]",
            border_style="yellow",
        ))
    console.print()


# ---------------------------------------------------------------------------
# Issues table
# ---------------------------------------------------------------------------

def show_issues_table(issues: list[Issue], proposals: list[FixProposal]):
    if not issues:
        console.print(Panel("[green]No issues found![/green]", style="green"))
        return

    proposal_map = {p.issue_id: p for p in proposals}

    table = Table(
        title=f"[bold]Detected Issues ({len(issues)})[/bold]",
        box=box.ROUNDED,
        show_lines=True,
        title_style="bold white",
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("ID", style="bold", width=6)
    table.add_column("Sev", width=9)
    table.add_column("Dim", width=13)
    table.add_column("Issue", width=38)
    table.add_column("Fix Preview", width=38)

    for i, issue in enumerate(issues, 1):
        sev_style = SEVERITY_COLORS.get(issue.severity, "white")
        dim_label = DIMENSION_LABELS.get(issue.dimension, issue.dimension)

        fix_preview = ""
        if issue.id in proposal_map:
            desc = proposal_map[issue.id].fix_description
            fix_preview = desc[:75] + ("..." if len(desc) > 75 else "")

        table.add_row(
            str(i),
            issue.id,
            Text(issue.severity.upper(), style=sev_style),
            dim_label,
            issue.title,
            fix_preview,
        )

    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# User selection (Human-in-the-Loop)
# ---------------------------------------------------------------------------

def get_user_selection(issues: list[Issue]) -> list[str]:
    console.print("[bold]Select issues to fix:[/bold]")
    console.print(
        "  Enter numbers (e.g. [cyan]1,3,5[/cyan]), "
        "[cyan]all[/cyan] to fix everything, or "
        "[cyan]none[/cyan] to skip fixes."
    )
    console.print()

    choice = console.input("[bold cyan]Your selection: [/bold cyan]").strip().lower()

    if choice in ("none", "n"):
        return []
    if choice in ("all", "a"):
        return [issue.id for issue in issues]

    try:
        indices = [int(x.strip()) for x in choice.split(",")]
        selected = []
        for idx in indices:
            if 1 <= idx <= len(issues):
                selected.append(issues[idx - 1].id)
            else:
                console.print(f"[yellow]Skipping invalid number: {idx}[/yellow]")
        return selected
    except ValueError:
        console.print("[red]Invalid input. Selecting all issues.[/red]")
        return [issue.id for issue in issues]


# ---------------------------------------------------------------------------
# Fix validation
# ---------------------------------------------------------------------------

def show_fix_validation(validation: FixValidation):
    method_label = METHOD_LABELS.get(validation.method, validation.method)

    if validation.applied and validation.assertion_passed:
        status = f"[green]applied[/green] ({method_label}) — assertion [green]PASSED[/green]"
    elif validation.applied:
        status = f"[yellow]applied[/yellow] ({method_label}) — assertion [red]FAILED[/red]"
    else:
        status = f"[red]not applied[/red] ({method_label})"

    console.print(f"  [bold]{validation.issue_id}[/bold]: {status}")

    # Approximate matches deserve explicit visibility — the user should know
    # when a fix landed on a block that was only a close resemblance.
    if validation.method == "fuzzy_anchor" and validation.match_confidence is not None:
        console.print(
            f"    [yellow]⚠ fuzzy anchor match at "
            f"{validation.match_confidence:.0%} similarity — verify manually[/yellow]"
        )
    elif validation.method == "llm_fallback":
        console.print(
            "    [yellow]⚠ fix applied via LLM fallback on a local section — verify manually[/yellow]"
        )

    if not validation.assertion_passed and validation.explanation:
        console.print(f"    [dim]{validation.explanation[:160]}[/dim]")


# ---------------------------------------------------------------------------
# Fix progress (legacy compat)
# ---------------------------------------------------------------------------

def show_fix_progress(issue_id: str, status: str):
    color = "green" if status == "applied" else "yellow"
    console.print(f"  [{color}]{issue_id}[/]: {status}")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def show_verification_header():
    console.print()
    console.print(Panel(
        "[bold]Behavioral Probe Verification[/bold]\n"
        "Testing each fix with a mid-workflow probe — comparing tool calls, "
        "conditions checked, and agent behavior between original and fixed prompts.",
        style="magenta",
    ))
    console.print()


def show_verdict(verdict: VerificationResult):
    score = verdict.improvement_score
    category = getattr(verdict, "verdict_category", "unchanged")
    # Label + color driven by the judge's 4-way category so inconclusive
    # (probe didn't trigger the bug) renders distinctly from unchanged.
    category_styles = {
        "improved": ("bold green", "IMPROVED"),
        "inconclusive": ("bold blue", "INCONCLUSIVE"),
        "unchanged": ("bold yellow", "UNCHANGED"),
        "regressed": ("bold red", "REGRESSED"),
    }
    score_style, score_label = category_styles.get(category, ("bold yellow", category.upper()))

    # Structural check status
    struct = "[green]PASS[/green]" if verdict.structural_pass else "[red]FAIL[/red]"
    behav = "[green]PASS[/green]" if verdict.behavioral_pass else "[red]FAIL[/red]"

    # Header with both check results
    console.print(Panel(
        f"[bold]Structural:[/bold] {struct}  |  [bold]Behavioral:[/bold] {behav}",
        title=f"[bold]{verdict.issue_id}[/bold]",
        border_style="dim",
    ))

    # Side-by-side probe responses
    orig = verdict.original_probe_response
    fixed = verdict.fixed_probe_response

    orig_panel = Panel(
        orig[:800] + ("..." if len(orig) > 800 else ""),
        title="[red]Original Behavior[/red]",
        border_style="red",
    )
    fixed_panel = Panel(
        fixed[:800] + ("..." if len(fixed) > 800 else ""),
        title="[green]Fixed Behavior[/green]",
        border_style="green",
    )
    console.print(Columns([orig_panel, fixed_panel], equal=True, expand=True))

    # Score
    console.print(
        f"  Score: [{score_style}]{score}/10 — {score_label}[/]\n"
        f"  {verdict.explanation}"
    )
    if verdict.remaining_concerns:
        console.print(f"  [dim]Remaining: {verdict.remaining_concerns}[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------

def show_final_summary(
    verdicts: list[VerificationResult],
    validations: list[FixValidation],
    output_dir: str,
    total_issues: int,
    selected_count: int,
):
    if not verdicts and not validations:
        console.print(Panel("[yellow]No verification performed.[/yellow]"))
        return

    applied_count = sum(1 for v in validations if v.applied)
    validated_count = sum(1 for v in validations if v.applied and v.assertion_passed)

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
        sum(v.improvement_score for v in scorable) / len(scorable)
        if scorable else 0
    )

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Issues detected", str(total_issues))
    table.add_row("Issues selected for fix", str(selected_count))
    table.add_row("Fixes applied", f"{applied_count}/{selected_count}")
    table.add_row("Fixes validated (assertion passed)", f"{validated_count}/{applied_count}")

    if verdicts:
        total = len(verdicts)
        improved_color = "green" if improved == total else "yellow"
        table.add_row(
            "Improved (probe demonstrated fix worked)",
            f"[{improved_color}]{improved}/{total}[/]",
        )
        if inconclusive:
            # Inconclusive ≠ failure; surface separately so the user doesn't
            # misread these as broken fixes.
            table.add_row(
                "Inconclusive (probe didn't trigger the bug)",
                f"[blue]{inconclusive}/{total}[/]",
            )
        if unchanged:
            table.add_row(
                "Unchanged (fix did not alter behavior)",
                f"[yellow]{unchanged}/{total}[/]",
            )
        if regressed:
            table.add_row(
                "Regressed (fix introduced a new problem)",
                f"[red]{regressed}/{total}[/]",
            )
        table.add_row(
            "Average score (excludes inconclusive)",
            f"[bold]{avg_score:.1f}/10[/bold]",
        )

    table.add_row("Report", f"{output_dir}/report.json")
    table.add_row("Fixed prompt", f"{output_dir}/fixed_prompt.json")

    console.print(Panel(
        table,
        title="[bold]Results Summary[/bold]",
        style="green",
        box=box.DOUBLE,
    ))


def show_error(message: str):
    console.print(f"[bold red]Error:[/bold red] {message}")


# ---------------------------------------------------------------------------
# LLM token stats
# ---------------------------------------------------------------------------

def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _sum_stats(stats: dict) -> dict:
    total = {"calls": 0, "input": 0, "cache_read": 0, "cache_create": 0, "output": 0}
    for row in stats.values():
        for k in total:
            total[k] += row.get(k, 0)
    return total


def show_pass_stats(delta: dict, label: str) -> None:
    """One-line summary of LLM usage incurred during a single pass."""
    if not delta:
        return
    total = _sum_stats(delta)
    cached_pct = (
        100 * total["cache_read"] / (total["input"] + total["cache_read"] + total["cache_create"])
        if (total["input"] + total["cache_read"] + total["cache_create"]) > 0
        else 0
    )
    console.print(
        f"  [dim][{label} LLM][/dim] "
        f"calls={total['calls']}  "
        f"in={_fmt_num(total['input'])}  "
        f"cache_read={_fmt_num(total['cache_read'])}  "
        f"cache_create={_fmt_num(total['cache_create'])}  "
        f"out={_fmt_num(total['output'])}  "
        f"[dim](cache_read={cached_pct:.0f}% of input)[/dim]"
    )


def show_llm_stats_summary(stats: dict) -> None:
    """Final per-model + total breakdown of LLM usage for the whole run."""
    if not stats:
        return

    table = Table(
        title="[bold]LLM Token Usage[/bold]",
        box=box.SIMPLE,
        title_style="bold white",
    )
    table.add_column("Model", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Input", justify="right")
    table.add_column("Cache Read", justify="right", style="green")
    table.add_column("Cache Create", justify="right", style="yellow")
    table.add_column("Output", justify="right")

    for model, row in sorted(stats.items()):
        table.add_row(
            model,
            str(row["calls"]),
            _fmt_num(row["input"]),
            _fmt_num(row["cache_read"]),
            _fmt_num(row["cache_create"]),
            _fmt_num(row["output"]),
        )

    total = _sum_stats(stats)
    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]",
        f"[bold]{total['calls']}[/bold]",
        f"[bold]{_fmt_num(total['input'])}[/bold]",
        f"[bold]{_fmt_num(total['cache_read'])}[/bold]",
        f"[bold]{_fmt_num(total['cache_create'])}[/bold]",
        f"[bold]{_fmt_num(total['output'])}[/bold]",
    )

    console.print()
    console.print(table)

    # Cache-hit interpretation note
    total_in = total["input"] + total["cache_read"] + total["cache_create"]
    if total_in > 0:
        cached_pct = 100 * total["cache_read"] / total_in
        console.print(
            f"  [dim]{cached_pct:.1f}% of input tokens served from cache "
            f"(cache reads cost ~10% of full input rate).[/dim]"
        )
