# Project Overview — Agentic Prompt Improver

Quick-context primer for future sessions. Skim this first, then open the files it points to.

## The Assignment

From `docs/task.txt`: build an **agentic** (not checklist) tool that takes a voice-agent system prompt (JSON, ~8–12K tokens, healthcare front-desk use case in the sample), autonomously finds quality issues across **Patient/Caller Experience** and **Workflow Adherence**, proposes precise fixes, lets the user choose what to apply, and **proves** the fixes actually change behavior. It must generalize — the graders test on a second prompt we haven't seen. Deliverables: working CLI, results, half-page README.

The sample prompt is `docs/assignment-agent-prompt.json`. `docs/harborview-agent-prompt.json` is a second healthcare prompt for generalization testing.

## Plan Evolution (PLAN → PLAN_V4)

All four plans have been implemented. Read them in order only if you need archaeology — current behavior is what the code does now.

- **`docs/PLAN.md`** — Initial 5-pass design: Detection + reflection → Analysis → User selection → Fix engine (verbatim string replace) → Verification (single-turn adversarial caller + LLM judge comparing response text). Single-turn chosen over multi-turn for cost.

- **`docs/PLAN_V2.md`** — Rewrite after run-1 showed only 12.5% of fixes actually improved behavior (avg score 2.4/10). Root-caused 6 failure modes: tool-parameter changes invisible in response text, adversarial scenarios started too early in the flow, simulators self-corrected buggy prompts, verbatim string matching too brittle, no validation before verification, fix proposals conflated intent with text surgery. Solutions: **anchor-based fixes** (20–40 char unique substrings + fix_type + new_content), **assertion-based structural validation**, **mid-workflow behavioral probes** that ask the agent to describe TOOL_CALLS with params (so param-level fixes are observable).

- **`docs/PLAN_V3.md`** — Added **Pass 0: Establish Guiding Principles**. Introduced curated canonical library (`principles.md`) of ~25 principles in categories (STRUCT/ROLE/TOOL/ELIG/STYLE/GUARD/CONTENT/SAFE/CONSIST/EX), an adaptive brief via Haiku selecting the load-bearing subset for the input prompt, new `principles` dimension for issues, prompt-caching via `cache_control` blocks on the static library. Principles-dimension issues skip the behavioral probe (assertion-only verification).

- **`docs/PLAN_V4.md`** — Senior-engineer critique + tiered roadmap. Tier 1 shipped: anchor uniqueness bug fixed (refuses to guess on ambiguity), fuzzy-match visibility, proper exception logging, score→pass threshold (≥7), deterministic **tool schema registry** (`schema_registry.py`). Tier 2 largely shipped: iteration loop (`--iterate N` with verdict feedback), regression sweep, domain-agnostic detection prompt with domain inference + non-healthcare warning. Tier 3+4 mostly open (see "Known gaps").

## Current Implementation

Six passes, orchestrated in `main.py`. Each feeds the next.

| Pass | File/Function | Model | Purpose |
|---|---|---|---|
| 0 | `agents.establish_principles` | Haiku 4.5 | Infer modality + domain, select ≤12 load-bearing principles, build deterministic `tool_schema_registry` from tool defs. Deterministic fallback if the LLM call fails. |
| 1 | `agents.detect` + self-reflection | Sonnet 4.6 | Flags issues across caller experience, workflow adherence, principles violations, schema/prose mismatches. Second call critiques and prunes false positives. |
| 2 | `agents.analyze` | Sonnet 4.6 | Proposes anchor-based fixes with `assertion` + `behavioral_probe`. Accepts `retry_feedback` on iteration. |
| 3 | `agents.apply_fixes` | local + Sonnet | Anchor lookup: exact → fuzzy (surface ratio) → LLM-fallback on a local window. Rejects ambiguous anchors rather than guessing. Validates each fix's assertion against fixed text. |
| 4 | `agents.verify` | Sonnet 4.6 | For non-principles issues: mid-workflow probe against original and fixed prompt, independent judge scores 1–10 with 4-way verdict category (improved/inconclusive/unchanged/regressed). Principles issues route to assertion-only. |
| 5 | `main._regression_sweep` | Sonnet 4.6 | Re-verify previously-passing fixes against the final prompt text; flag drops as `regressed`. |

With `--iterate N`, failed or inconclusive fixes re-enter Pass 2 with structured judge feedback (`main._format_retry_feedback`) up to N rounds before the regression sweep runs.

### Modules

- **`main.py`** (~540 lines) — Click CLI, pre-flight checks, Pass orchestration, `_run_fix_verify_loop` (iteration + feedback wiring), `_regression_sweep`, `_write_report` (report.json / fixed_prompt.json / prompt.diff).

- **`agents.py`** (~1430 lines) — all LLM passes. `LLM` wrapper with per-model token stats + streaming for large outputs + `call_json` with retry-on-parse-failure. `_cached_block` for prompt caching. Detection/reflection/analysis/probe/judge prompts. Fix engine with anchor disambiguation + fuzzy fallback + LLM fallback. Judge is kept **channel-agnostic** — it sees behaviors + expected change only, not the prompt.

- **`models.py`** — Pydantic types. Dimensions: `patient_experience` / `workflow_adherence` / `principles`. `PrinciplesBrief` carries modality, domain, domain_signals, active_principles, interaction_contract, structure_notes, tool_schema_registry. `FixProposal` is anchor-based. `VerificationResult` has `verdict_category`, `iteration`, `regressed`.

- **`schema_registry.py`** — Deterministic extractor. Walks OpenAI/Vapi/Retell tool shapes, emits a compact `<tool_schema_registry>` text block listing every param with type/format/enum/required/description hint. Injected into detection so schema↔prose mismatches (e.g. `DD-MM-YYYY` vs `date` format) are caught systematically.

- **`principles.py`** + **`principles.md`** — `principles.py` is a 7-line module that reads the markdown library at import time into `CANONICAL_PRINCIPLES_TEXT`. The `.md` file holds the ~25 curated principles (STRUCT/ROLE/TOOL/ELIG/STYLE/GUARD/CONTENT/SAFE/CONSIST/EX) with rule, rationale, violation signature, and citation per entry.

- **`loader.py`** — Vendor-agnostic prompt loading. Tries known keys (`general_prompt`, `system_prompt`, `prompt`, `instructions`, `content`) then falls back to longest string value. Handles Vapi/Retell/Bland/ElevenLabs/custom JSON/plain text.

- **`ui.py`** — Rich-based rendering. Step headers, principles summary, issues table, fix validation rows with fuzzy-match confidence, verdict display, per-pass LLM stats with cache-hit ratios, final summary counting improved/inconclusive/unchanged/regressed + avg score (excluding inconclusive).

## CLI & Outputs

```bash
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python3 main.py docs/assignment-agent-prompt.json \
    [--auto-fix]     # no interactive selection
    [--dry-run]      # detect only
    [--iterate N]    # max iterations (default 1)
    [--multi-turn N] # caller turns per probe (default 1)
    [--output-dir DIR]
    [-v]
```

Writes to `output/` (or `--output-dir`):
- `report.json` — full structured run (principles brief, detection, analysis, validations, verdicts, per-iteration metadata, summary).
- `fixed_prompt.json` — original JSON with the prompt field replaced.
- `prompt.diff` — unified diff between original and final prompt text.

## Key Design Decisions

- **Anchor disambiguation refuses to guess.** If an anchor appears in multiple places and `anchor_context` can't pick a winner, the fix fails cleanly.
- **Judge stays channel-agnostic.** Simulator sees the prompt; judge sees only the two behaviors + expected change + strict 1–10 rubric (≥7 counts as pass).
- **Pipeline is a loop, not a line.** Failing fixes re-enter analysis with verdict feedback; passing fixes are re-verified against the final text to catch regressions from overlapping edits.
- **Structured tool schema registry.** Schema↔prose mismatches are surfaced via a deterministic parser, not by the LLM noticing.
- **Domain-agnostic prompts, healthcare-tuned library.** Detection/analysis prompts are caller-neutral; healthcare-specific fallback principles only fire on healthcare signals. Non-healthcare domain triggers a warning panel.
- **Prompt caching.** Canonical principles block wrapped in one ephemeral cache block shared across passes. Typical cache-read ratio 75–95%.
- **Minimal surface.** Six modules, no framework, no DAG runner, no vector store.

## Agentic Patterns Used

| Pattern | Where |
|---|---|
| Prompt chaining | Each pass consumes structured output of the prior one |
| Reflection | Detection self-critiques before fix proposals |
| Plan–Execute | All fixes proposed before any are applied |
| Human-in-the-Loop | User picks fixes before mutation |
| LLM-as-judge | Independent judge scores behavior deltas |
| Iteration + self-correction | `--iterate` re-analyzes failures with structured verdict feedback |

## Models + Cost

- **Haiku 4.5** for Pass 0 (cheap, short structured output).
- **Sonnet 4.6** for detection/reflection/analysis/probe/judge.
- Default run (`--iterate 1 --multi-turn 1`) on the ~8K-token assignment prompt: ~5–7 calls, under $0.30. `--iterate 3 --multi-turn 3` ≈ 4×.

## Known Gaps (what's still open)

From `docs/PLAN_V4.md` Tier 3+4 and the README "what I'd improve":

1. Migrate critical `call_json` sites to Anthropic `tools` + `tool_choice` for schema-enforced outputs (eliminate JSON parse retries).
2. Async/parallel probe verification (`asyncio.gather`) — current sequential loop is the longest wall-clock segment.
3. Richer multi-turn probe with adaptive caller persona + stop conditions.
4. `--max-cost` flag that aborts gracefully before exceeding budget.
5. Unit test suite for anchor disambiguation, fuzzy matching, schema registry extraction, loader edge cases.
6. Non-healthcare regression prompts (fintech fraud check, SaaS cancel) committed + run in CI.
7. LLM-as-judge calibration against a small annotated set.

## Where to Go for Deeper Dives

- **Assignment intent** → `docs/task.txt`
- **Most-current critique + roadmap** → `docs/PLAN_V4.md`
- **Architectural decisions archaeology** → `docs/PLAN_V2.md` (anchor fixes + behavioral probes), `docs/PLAN_V3.md` (principles + caching)
- **How to run** → `README.md`
- **All LLM prompts + fix engine** → `agents.py`
- **Canonical principles content** → `principles.md`
- **Sample inputs** → `docs/assignment-agent-prompt.json`, `docs/harborview-agent-prompt.json`
- **Run outputs** → `output/report.json` (full structured run after any execution)
