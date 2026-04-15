"""Write the three end-of-run artifacts: report.json, fixed_prompt.json, prompt.diff.

Isolated from `main.py` so the CLI stays readable.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Optional

import agents
from . import ui
from .pipeline import build_summary


def write_report(
    output_dir: str,
    detection,
    analysis,
    selected_ids,
    validations,
    verdicts,
    data,
    brief,
    original_prompt_text: str,
    final_prompt_text: str,
    memory_info: Optional[dict] = None,
) -> None:
    """Emit `report.json`, `fixed_prompt.json`, and `prompt.diff` under `output_dir`."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    report = {
        "principles_brief": brief.model_dump() if brief else None,
        "llm_stats": agents.llm.snapshot(),
        "detection": detection.model_dump() if detection else None,
        "analysis": analysis.model_dump() if analysis else None,
        "selected_issues": selected_ids,
        "validations": [v.model_dump() for v in validations] if validations else [],
        "verdicts": [v.model_dump() for v in sorted(verdicts, key=lambda x: x.issue_id)] if verdicts else [],
        "summary": build_summary(detection, selected_ids, validations, verdicts),
    }
    if memory_info is not None:
        report["memory"] = memory_info

    (out / "report.json").write_text(json.dumps(report, indent=2))

    if selected_ids and data:
        (out / "fixed_prompt.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False)
        )

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
