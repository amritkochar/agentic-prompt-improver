"""Rich terminal rendering + interactive selection."""

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from models import FixProposal, Issue, JudgeVerdict

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
# Fix progress
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
        "[bold]Adversarial Verification[/bold]\n"
        "Generating targeted test scenarios, simulating responses, judging improvement.",
        style="magenta",
    ))
    console.print()


def show_verdict(verdict: JudgeVerdict):
    score = verdict.improvement_score
    if score >= 7:
        score_style = "bold green"
        score_label = "IMPROVED"
    elif score >= 4:
        score_style = "bold yellow"
        score_label = "PARTIAL"
    else:
        score_style = "bold red"
        score_label = "NO CHANGE"

    # Scenario header
    console.print(Panel(
        f"[bold]Scenario:[/bold] {verdict.scenario_description}\n"
        f"[bold]Caller:[/bold] \"{verdict.user_message}\"",
        title=f"[bold]{verdict.issue_id}[/bold]",
        border_style="dim",
    ))

    # Side-by-side responses (strip TRIGGERED_ISSUE line for display)
    orig = verdict.original_response.split("TRIGGERED_ISSUE:")[0].strip()
    fixed = verdict.fixed_response.split("TRIGGERED_ISSUE:")[0].strip()

    orig_panel = Panel(
        orig[:600] + ("..." if len(orig) > 600 else ""),
        title="[red]Original[/red]",
        border_style="red",
    )
    fixed_panel = Panel(
        fixed[:600] + ("..." if len(fixed) > 600 else ""),
        title="[green]Fixed[/green]",
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
    verdicts: list[JudgeVerdict],
    output_dir: str,
    total_issues: int,
    selected_count: int,
):
    if not verdicts:
        console.print(Panel("[yellow]No verification performed.[/yellow]"))
        return

    improved = sum(1 for v in verdicts if v.improvement_detected)
    avg_score = sum(v.improvement_score for v in verdicts) / len(verdicts)

    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Issues detected", str(total_issues))
    table.add_row("Issues selected for fix", str(selected_count))
    color = "green" if improved == len(verdicts) else "yellow"
    table.add_row("Verified improved", f"[{color}]{improved}/{len(verdicts)}[/]")
    table.add_row("Average improvement score", f"[bold]{avg_score:.1f}/10[/bold]")
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
