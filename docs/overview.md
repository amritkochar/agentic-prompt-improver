# Project Overview — Agentic Prompt Improver

Quick-context primer for future sessions. Skim this first, then open the files it points to.

## The Assignment

From `docs/task/task.txt`: build an **agentic** (not checklist) tool that takes a voice-agent system prompt (JSON, ~8–12K tokens, healthcare front-desk use case in the sample), autonomously finds quality issues across **Patient/Caller Experience** and **Workflow Adherence**, proposes precise fixes, lets the user choose what to apply, and **proves** the fixes actually change behavior. It must generalize — the graders test on a second prompt we haven't seen. Deliverables: working CLI, results, half-page README.

The sample prompt is `docs/prompts/assignment-agent-prompt.json`. A second healthcare prompt (`docs/prompts/harborview-agent-prompt.json`) was used for generalization testing.

## Plan Evolution (PLAN → PLAN_V4)

All four plans have been implemented. Read them in order only if you need archaeology — current behavior is what the code does now.

- **`docs/PLAN.md`** — Initial 5-pass design: Detection + reflection → Analysis → User selection → Fix engine (verbatim string replace) → Verification (single-turn adversarial caller + LLM judge comparing response text).

- **`docs/PLAN_V2.md`** — Rewrite after run-1 showed only 12.5% of fixes actually improved behavior (avg score 2.4/10). Root-caused 6 failure modes. Solutions: **anchor-based fixes** (20–40 char unique substrings + fix_type + new_content), **assertion-based structural validation**, **mid-workflow behavioral probes** that ask the agent to describe TOOL_CALLS with params.

- **`docs/PLAN_V3.md`** — Added **Pass 0: Establish Guiding Principles**. Introduced curated canonical library (`docs/principles.md`) of ~25 principles, adaptive brief via Haiku selecting the load-bearing subset, new `principles` dimension for issues, prompt-caching via `cache_control` blocks.

- **`docs/PLAN_V4.md`** — Senior-engineer critique + tiered roadmap. Tier 1 shipped: anchor uniqueness bug fixed, fuzzy-match visibility, proper exception logging, score→pass threshold (≥7), deterministic **tool schema registry**. Tier 2 largely shipped: iteration loop, regression sweep, domain-agnostic detection. Async parallel verification + unit test suite also completed.

## Current Implementation

Seven passes, orchestrated in `main.py` + `core/pipeline.py`. Each feeds the next.

| Pass | File/Function | Model | Purpose |
|---|---|---|---|
| 0 | `agents/principles_pass.py` | Haiku 4.5 | Infer modality + domain, select ≤12 load-bearing principles, build deterministic `tool_schema_registry` from tool defs. Deterministic fallback if the LLM call fails. |
| 0.5 | `core/memory.py` — `load_kg` + `select_relevant_lessons` | local | Load the persistent cross-run knowledge graph (`memory/knowledge_graph.md`) and pick lessons whose tags overlap this run's domain / modality / active principles / issue dimensions. No LLM call. |
| 1 | `agents/detect_pass.py` | Sonnet 4.6 | Flags issues across caller experience, workflow adherence, principles violations, schema/prose mismatches. Second call critiques and prunes false positives. |
| 2 | `agents/analyze_pass.py` | Sonnet 4.6 | Proposes anchor-based fixes with `assertion` + `behavioral_probe`. Receives the selected `<prior_run_lessons>` block; records `lessons_applied` IDs on proposals. Accepts `retry_feedback` on iteration. |
| 3 | `agents/fix_engine.py` | local + Sonnet | Anchor lookup: exact → fuzzy (surface ratio) → LLM-fallback on a local window. Rejects ambiguous anchors rather than guessing. Validates each fix's assertion against fixed text. |
| 4 | `agents/verify_pass.py` | Sonnet 4.6 | For non-principles issues: mid-workflow probe against original and fixed prompt, independent judge scores 1–10 with 4-way verdict category (improved/inconclusive/unchanged/regressed). Principles issues route to assertion-only. |
| 5 | `core/pipeline.py` — `regression_sweep` | Sonnet 4.6 | Re-verify previously-passing fixes against the final prompt text; flag drops as `regressed`. |
| 6 | `agents/memory_pass.py` → `core/memory.py` — `write_kg` | Haiku 4.5 | Merge this run's RunRecord + validations + verdicts into the KG: add/increment triples, consolidate recurring patterns into lessons, downgrade/retire contradicted lessons, trim to caps (30 lessons, 150 triples, 20 runs). Disable via `--no-memory-update` or `--no-memory`. |

With `--iterate N`, failed or inconclusive fixes re-enter Pass 2 with structured judge feedback up to N rounds before the regression sweep runs.

## Package Layout

```
agentic-prompt-improver/
├── main.py                     # CLI entry point, pass dispatch, KG wiring
├── agents/                     # One file per LLM pass
│   ├── __init__.py             # Re-exports public surface (agents.detect, etc.)
│   ├── llm.py                  # LLM wrapper — token stats, call_json retry, prompt caching
│   ├── prompts/                # Per-pass prompt templates (split from monolithic prompts.py)
│   │   ├── __init__.py         # Re-exports all constants for backward-compat imports
│   │   ├── principles.py       # Pass 0 — PRINCIPLES_INSTRUCTION
│   │   ├── detect.py           # Pass 1 — DETECTION_PROMPT, REFLECTION_PROMPT
│   │   ├── analyze.py          # Pass 2 — ANALYSIS_PROMPT
│   │   ├── fix.py              # Pass 3 — LLM_FIX_PROMPT, ASSERTION_CHECK_PROMPT
│   │   ├── verify.py           # Pass 4 — PROBE_PROMPT, JUDGE_PROMPT, FOLLOWUP_GENERATOR_PROMPT
│   │   └── memory.py           # Pass 6 — CONSOLIDATION_PROMPT
│   ├── principles_pass.py      # Pass 0
│   ├── detect_pass.py          # Pass 1
│   ├── analyze_pass.py         # Pass 2
│   ├── fix_engine.py           # Pass 3
│   ├── verify_pass.py          # Pass 4
│   └── memory_pass.py          # Pass 6
├── core/                       # Infrastructure — no LLM calls
│   ├── __init__.py
│   ├── loader.py               # Vendor-agnostic prompt loading (Vapi/Retell/plain text)
│   ├── models.py               # All Pydantic types for inter-pass typed data
│   ├── memory.py               # Knowledge-graph I/O, lesson selection, KG serialization
│   ├── pipeline.py             # run_fix_verify_loop, regression_sweep, build_summary
│   ├── principles.py           # Reads docs/principles.md into CANONICAL_PRINCIPLES_TEXT
│   ├── reporting.py            # Writes report.json, fixed_prompt.json, prompt.diff
│   ├── schema_registry.py      # Deterministic tool-parameter constraint extractor
│   └── ui.py                   # Rich-based terminal rendering
├── tests/                      # 91 unit tests — no API key needed, runs in ~0.3s
│   ├── test_fix_engine.py      # _fuzzy_find, anchor disambiguation, _apply_single_fix
│   ├── test_memory.py          # KG load/write round-trip, lesson selection, format
│   ├── test_loader.py          # Prompt loading, key extraction, agent name
│   └── test_schema_registry.py # Tool-parameter extraction, build_registry
├── docs/
│   ├── principles.md           # ~25 curated canonical principles (STRUCT/ROLE/TOOL/...)
│   ├── overview.md             # This file
│   ├── task/task.txt           # Assignment spec
│   └── prompts/                # Sample agent prompts for testing
├── memory/
│   └── knowledge_graph.md      # Persistent cross-run KG (lessons + triples + run log)
└── output/
    ├── run-greenfield-medical/ # Results: Greenfield Medical Clinic prompt
    └── run-carelink-homehealth/# Results: CareLink Home Health prompt (generalization)
```

## Module Details

- **`main.py`** (~250 lines) — Click CLI + pre-flight + top-level pass dispatch + KG wiring (load, inject, consolidate). Orchestration of the iteration loop lives in `core/pipeline.py`; report writing lives in `core/reporting.py`.

- **`core/pipeline.py`** — `run_fix_verify_loop` (Pass 3+4 with iteration + verdict feedback), `format_retry_feedback`, `regression_sweep` (Pass 5), `build_summary`. Pure coordination, no LLM prompts.

- **`core/reporting.py`** — `write_report` emits `report.json`, `fixed_prompt.json`, `prompt.diff`.

- **`core/memory.py`** — Knowledge-graph I/O + deterministic lesson selection. Parses/serialises the markdown KG, filters lessons to the current run's tags (`select_relevant_lessons`), formats them into a `<prior_run_lessons>` block for Pass 2, builds `RunRecord` summaries, enforces caps (30 lessons / 150 triples / 20 runs). Named constants: `MAX_LESSONS = 30`, `MAX_TRIPLES = 150`, `MAX_RUNS_IN_LOG = 20`, `MAX_LESSONS_INJECTED = 10`.

- **`agents/`** — One file per LLM pass. Prompt templates split into `agents/prompts/` subpackage (one file per pass) so each template is immediately findable without scrolling a 575-line monolith.
  - `agents/llm.py` — `LLM` wrapper with per-model token stats + streaming + `call_json` retry-on-parse. `_cached_block` helper for prompt caching. Module-level singleton `llm`.
  - `agents/prompts/` — Per-pass string constants. Import as `from .prompts import DETECTION_PROMPT` (unchanged for callers; `__init__.py` re-exports everything).
  - `agents/principles_pass.py` — Pass 0 (`establish_principles`) + deterministic fallback + `format_brief_for_passes` serializer.
  - `agents/detect_pass.py` — Pass 1 detection + reflection.
  - `agents/analyze_pass.py` — Pass 2 analysis (takes optional `lessons` and `retry_feedback`).
  - `agents/fix_engine.py` — Pass 3 anchor-based `apply_fixes` + assertion validation. Exact → fuzzy → LLM fallback. Refuses ambiguous anchors. Thresholds: `_FUZZY_THRESHOLD = 0.75`, `_FUZZY_LLM_THRESHOLD = 0.50`, `_MAX_BLOCK_SIZE = 2000`.
  - `agents/verify_pass.py` — Pass 4 probe + channel-agnostic judge. Principles issues route to assertion-only. Parallel via `asyncio.gather`.
  - `agents/memory_pass.py` — Pass 6 Haiku consolidation call; safe no-op on failure.

- **`core/models.py`** — Pydantic v2 types for all inter-pass data. Dimensions: `patient_experience` / `workflow_adherence` / `principles`. Key models: `PrinciplesBrief`, `Issue`, `FixProposal` (anchor-based, carries `lessons_applied`), `FixValidation`, `VerificationResult` (has `verdict_category`, `iteration`, `regressed`). KG types: `Lesson`, `Triple`, `RunRecord`, `KnowledgeGraph`, `KGUpdate`.

- **`core/schema_registry.py`** — Deterministic extractor. Walks OpenAI/Vapi/Retell tool shapes, emits a compact `<tool_schema_registry>` text block listing every param with type/format/enum/required/description hint. Injected into detection so schema↔prose mismatches (e.g. `DD-MM-YYYY` vs `date` format) are caught systematically.

- **`core/principles.py`** + **`docs/principles.md`** — `principles.py` is a 7-line module that reads the markdown library at import time into `CANONICAL_PRINCIPLES_TEXT`. The `.md` file holds the ~25 curated principles (STRUCT/ROLE/TOOL/ELIG/STYLE/GUARD/CONTENT/SAFE/CONSIST/EX) with rule, rationale, violation signature, and citation per entry.

- **`core/loader.py`** — Vendor-agnostic prompt loading. Tries known keys (`general_prompt`, `system_prompt`, `prompt`, `instructions`, `content`) then falls back to longest string value. Handles Vapi/Retell/Bland/ElevenLabs/custom JSON/plain text.

- **`core/ui.py`** — Rich-based rendering. Step headers, principles summary, issues table, fix validation rows with fuzzy-match confidence, verdict display, per-pass LLM stats with cache-hit ratios, final summary counting improved/inconclusive/unchanged/regressed + avg score (excluding inconclusive).

## CLI & Outputs

```bash
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python3 main.py docs/prompts/assignment-agent-prompt.json \
    [--auto-fix]        # no interactive selection
    [--dry-run]         # detect only
    [--iterate N]       # max iterations (default 1)
    [--multi-turn N]    # caller turns per probe (default 1)
    [--concurrency N]   # max parallel LLM calls in Pass 4/5 (default 5)
    [--output-dir DIR]  # default: output/
    [-v]                # verbose logging
```

Writes to `output/<run-dir>/`:
- `report.json` — full structured run (principles brief, detection, analysis, validations, verdicts, per-iteration metadata, memory summary).
- `fixed_prompt.json` — original JSON with the prompt field replaced.
- `prompt.diff` — unified diff between original and final prompt text.

### Running tests

```bash
python3 -m pytest tests/ -v              # all 91 tests, ~0.3s, no API key needed
python3 -m pytest tests/test_fix_engine.py -v
python3 -m pytest tests/ -k "fuzzy" -v
```

## Cross-Run Memory (Knowledge Graph)

Every run reads `memory/knowledge_graph.md` before analysis and writes it back after verification. The file has three sections:

- **Lessons** — semantic layer. Consolidated rules with `confidence` + `support`, e.g. *"Fixing date-format bugs via insert_after near the tool-call paragraph beats replace on the whole tool section (3/3 runs). Confidence: medium"*.
- **Triples** — episodic edges `(head, relation, tail)` with support counts. Heads/tails are structured strings like `fix_strategy:"replace_large_block"`, `verdict:"unchanged"`, `principle:"TOOL-04"`, `domain:"healthcare"`.
- **Run log** — append-only summary of the last 20 runs (domain, counts, notes).

**Selection** (Pass 0.5, deterministic): lessons are ranked by `tag_overlap × confidence_weight × support` against the current run's domain / modality / active-principle IDs / issue dimensions. Top 10 survive.

**Injection** (Pass 2): surviving lessons are rendered as a `<prior_run_lessons>` block in `analyze()`'s user content. Proposals list `lessons_applied` when a lesson directly shaped the fix.

**Consolidation** (Pass 6, Haiku): after the regression sweep, the Haiku consolidation pass takes the current KG + this run's `RunRecord` + per-issue outcomes and returns an updated `(lessons, triples)` set — incrementing support, downgrading contradicted lessons, retiring stale low-confidence ones, capped at 30 lessons / 150 triples. `--no-memory` skips load+write entirely; `--no-memory-update` reads but does not write.

## Key Design Decisions

- **Anchor disambiguation refuses to guess.** If an anchor appears in multiple places and `anchor_context` can't pick a winner, the fix fails cleanly rather than silently landing in the wrong section. Fuzzy matches surface their similarity ratio to the user.
- **Judge stays channel-agnostic.** Simulator sees the prompt; judge sees only the two behaviors + expected change + strict 1–10 rubric (≥7 counts as pass). Partial improvements (4-6) are visible but don't flip the pass flag.
- **Pipeline is a loop, not a line.** Failing fixes re-enter analysis with verdict feedback; passing fixes are re-verified against the final text to catch regressions from overlapping edits.
- **Structured tool schema registry.** Schema↔prose mismatches are surfaced via a deterministic parser, not by the LLM noticing.
- **Domain-agnostic prompts, healthcare-tuned library.** Detection/analysis prompts are caller-neutral; healthcare-specific fallback principles only fire on healthcare signals. Non-healthcare domain triggers a warning panel.
- **Prompt caching.** Canonical principles block wrapped in one ephemeral cache block shared across passes. Typical cache-read ratio 75–95%.
- **Parallel verification.** Pass 4 and Pass 5 run probe traces concurrently via `asyncio.gather` / `as_completed`; original-prompt and fixed-prompt probes within a single verification also run in parallel. Rate-limited via semaphore (`--concurrency`, default 5).
- **Two clean packages.** `agents/` owns all LLM passes; `core/` owns all infrastructure with no LLM calls. `agents/prompts/` subpackage puts each pass's templates in their own file.

## Agentic Patterns Used

| Pattern | Where |
|---|---|
| Prompt chaining | Each pass consumes structured output of the prior one |
| Reflection | Detection self-critiques before fix proposals |
| Plan–Execute | All fixes proposed before any are applied |
| Human-in-the-Loop | User picks fixes before mutation |
| LLM-as-judge | Independent judge scores behavior deltas |
| Iteration + self-correction | `--iterate` re-analyzes failures with structured verdict feedback |
| Cross-run memory | KG loaded into analysis, updated after verification |
| Parallel tool use | Independent probe traces execute concurrently within a bounded semaphore |

## Models + Cost

- **Haiku 4.5** for Pass 0 and Pass 6 (cheap, structured output).
- **Sonnet 4.6** for detection/reflection/analysis/probe/judge.
- Default run (`--iterate 1 --multi-turn 1`) on the ~8K-token assignment prompt: ~5–7 Sonnet calls + 2 Haiku calls, under $0.30. `--iterate 3 --multi-turn 3` ≈ 4×. KG consolidation adds ~$0.02 per run.

## Open Gaps

1. Migrate critical `call_json` sites to Anthropic `tools` + `tool_choice` for schema-enforced outputs (eliminate JSON parse retries).
2. Richer multi-turn probe with adaptive caller persona (escalating / disengaged / confused) + stop conditions.
3. `--max-cost` flag that aborts gracefully before the next paid call once exceeded.
4. Non-healthcare regression prompts (fintech fraud check, SaaS cancel) committed + run in CI to keep domain-agnostic path honest.
5. LLM-as-judge calibration against a small annotated (original, fixed, ground-truth-score) set; adjust rubric weight where it drifts.
6. Post-apply seam smoother — optional LLM pass after all fixes land that touches only grammar/tone at edit boundaries.

## Where to Go for Deeper Dives

- **Assignment intent** → `docs/task/task.txt`
- **Most-current critique + roadmap** → `docs/PLAN_V4.md`
- **Architectural decisions archaeology** → `docs/PLAN_V2.md` (anchor fixes + behavioral probes), `docs/PLAN_V3.md` (principles + caching)
- **How to run** → `README.md`
- **All LLM prompts** → `agents/prompts/` (one file per pass)
- **Canonical principles content** → `docs/principles.md`
- **Sample inputs** → `docs/prompts/assignment-agent-prompt.json`, `docs/prompts/harborview-agent-prompt.json`
- **Run outputs** → `output/run-greenfield-medical/`, `output/run-carelink-homehealth/`
