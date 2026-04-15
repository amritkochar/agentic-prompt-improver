# Voice Agent Prompt Quality Tool

Agentic CLI that analyzes a conversational agent system prompt, detects real quality issues across caller experience and workflow adherence, proposes targeted fixes, and verifies each fix actually changes agent behavior via adversarial probe + independent LLM judge.

## Run

```bash
pip3 install -r requirements.txt
cp .env.example .env          # then edit .env and set your ANTHROPIC_API_KEY
python3 main.py docs/assignment-agent-prompt.json
```

### Options

```bash
python3 main.py PROMPT.json \
    [--auto-fix]              # apply all fixes without prompting
    [--dry-run]               # detect only, no fixes or verification
    [--iterate N]             # max analyze/apply/verify iterations (default 1)
    [--multi-turn N]          # caller turns per behavioral probe (default 1)
    [--concurrency N]         # max parallel LLM calls in Pass 4/5 (default 5; 1 = sequential)
    [--memory-file PATH]      # knowledge-graph file (default memory/knowledge_graph.md)
    [--no-memory]             # skip KG read + write this run
    [--no-memory-update]      # read KG but do not update it after this run
    [--output-dir DIR]        # default: output
    [-v]                      # verbose logging
```

## Outputs (in `output/`)

- `report.json` — full structured run: principles brief, detection, analysis, validations, verdicts, per-iteration metadata, memory summary (lessons applied / added / retired). Verdicts sorted by issue_id for stable diffs.
- `fixed_prompt.json` — original JSON with the prompt field replaced.
- `prompt.diff` — unified diff between original and final prompt text.

## How It Works

Seven passes, each feeds the next; human-in-the-loop between detection and fixing.

| Pass | Purpose |
|---|---|
| 0. Establish Principles | Haiku selects the load-bearing canonical principles (≤12), infers modality + domain, builds a deterministic tool-schema registry from the tool definitions. |
| 0.5. Load Prior-Run Lessons | Parse the persistent knowledge graph (`memory/knowledge_graph.md`), filter lessons by domain/modality/active-principle overlap, render into a `<prior_run_lessons>` block for Pass 2. No LLM call. |
| 1. Detection + Reflection | Sonnet flags issues across caller experience, workflow adherence, and principles violations; a second Sonnet call critiques the list, merges duplicates, and removes non-reproducible items. |
| 2. Fix Analysis | Sonnet proposes minimal fixes with a unique anchor, verifiable assertion, and an adversarial mid-workflow behavioral probe (probe-design checklist enforced). Prior-run lessons are injected; `lessons_applied` is recorded per proposal. |
| 3. Fix Engine + Validation | Anchor-based text replacement with ambiguity rejection + fuzzy fallback + LLM fallback; each fix validated against its assertion. |
| 4. Behavioral Probe | For non-principles issues: single- or multi-turn probe simulated against both original and fixed prompt; independent judge scores 1-10 with a 4-way verdict category (improved / inconclusive / unchanged / regressed). **Issues probed in parallel** (asyncio + concurrency cap). |
| 5. Regression Check | Previously-passing fixes are re-verified against the final prompt text; drops are flagged `regressed`. Parallelized. |
| 6. Knowledge-Graph Consolidation | Haiku merges this run's outcomes into the KG: increment support on matching lessons/triples, downgrade contradicted lessons, retire stale low-confidence entries, cap at 30 lessons / 150 triples / 20 runs. Skipped by `--no-memory` or `--no-memory-update`. |

With `--iterate N`, failed fixes go back through passes 2-4 with structured verdict feedback up to N rounds before the regression sweep runs.

## Key Design Decisions

- **Structured tool-schema registry (`schema_registry.py`).** Parse parameter formats, enums, required fields, and description hints deterministically; inject them into detection so schema/prose mismatches (e.g. prompt says `DD-MM-YYYY`, tool schema says `date`) are caught systematically rather than relying on the LLM to notice.
- **Anchor disambiguation refuses to guess.** If an anchor appears in multiple places and `anchor_context` doesn't clearly pick a winner, the fix fails cleanly rather than silently landing in the wrong section. Fuzzy matches surface their similarity ratio to the user.
- **Judge stays channel-agnostic.** The simulator sees the system prompt; the judge sees only the two behaviors and the expected change, and uses a strict 1-10 rubric where only ≥7 counts as a behavioral pass. Partial improvements (4-6) are visible but don't flip the pass flag.
- **Probe-design discipline.** Pass 2 is required to produce probes with an adversarial lure that defeats schema-based guessing, and the simulator prompt (Pass 4) forbids silent self-correction — both attack the "inconclusive" verdict at its root. Pass 2 also ships with a reproducibility test in detection: an issue that can't be exercised by a concrete scenario is dropped before it ever reaches analysis.
- **Iteration + regression.** Pipeline is a loop, not a line. Failing fixes re-enter analysis with the prior verdict as feedback; passing fixes are re-verified against the final text so overlapping edits can't silently regress earlier wins.
- **Cross-run memory (knowledge graph).** `memory/knowledge_graph.md` accumulates consolidated lessons, (head, relation, tail) triples with support counts, and an episodic run log across invocations. Pass 0.5 filters lessons by tag overlap with the current run's domain/modality/active principles and injects the top 10 into analysis; Pass 6 merges this run's outcomes back in (Haiku, <$0.02). Human-readable markdown — no database, no vector store.
- **Parallel verification.** Pass 4 and Pass 5 run probe traces across issues concurrently via `asyncio.gather` / `as_completed`; within a single verification, the original-prompt and fixed-prompt probes also run in parallel. Rate-limited via a semaphore (`--concurrency`, default 5). Zero cost change vs sequential — same calls, same tokens.
- **Domain-agnostic prompts, healthcare-tuned library.** Detection/reflection/analysis prompts are written in caller-neutral language; the canonical principles library is healthcare-flavored but fallbacks only add healthcare-specific principles when healthcare signals are actually present. A warning panel fires when a non-healthcare domain is inferred.
- **Prompt caching.** The canonical principles text is large and static, so it's wrapped as a single ephemeral cache block shared across passes. Typical cache-read ratios are 75-95%.
- **Minimal surface.** Small package (`agents/` split by pass, plus `pipeline.py`, `reporting.py`, `memory.py`, `schema_registry.py`, `loader.py`, `ui.py`). No agent framework, no DAG runner, no vector store.

## Cost & Performance

A typical run on the ~8K-token assignment prompt with default flags (`--iterate 1 --multi-turn 1`) touches ~5-7 Sonnet calls + 2 Haiku calls and costs under $0.30 at current pricing. `--iterate 3 --multi-turn 3` roughly quadruples that. KG consolidation adds ~$0.02 per run.

**Wall clock.** Pass 4 verification — historically the long pole — runs all issue probes in parallel up to `--concurrency` (default 5), and each individual verification runs its original + fixed probes concurrently. End-to-end time on the assignment prompt typically drops ~40-50% vs sequential; Pass 4 itself drops ~4-5×. `--concurrency 1` reproduces the sequential behavior for debugging.

Model choice:
- **Haiku 4.5** for Pass 0 and Pass 6 (cheap, structured output).
- **Sonnet 4.6** everywhere else. Judge and analysis benefit from the extra reasoning; detection and reflection occasionally catch subtle bugs that Haiku misses.

## AI Tools Used

- **Claude Sonnet 4.6** / **Claude Haiku 4.5** via the Anthropic SDK for every agent pass; prompt caching via `cache_control` blocks.
- **Claude Code** (Sonnet 4.6) for the majority of the implementation — scaffolding, refactors, and review of the fix engine. Used in plan mode first to produce a critique + prioritized roadmap, then in execute mode to implement the changes against that plan.
- **OpenAI Prompt Optimizer** used during research to surface a handful of workflow/guardrail issues the baseline Detection prompt would have missed; those checks were folded into the detection rubric.

## What I'd Improve With More Time

Prioritized, with rough effort. (Parallel verification and cross-run memory — previously on this list — are now shipped.)

1. **Tool-use structured outputs (≈3h).** Migrate the critical `call_json` sites (detection, analysis, judge, consolidation) to Anthropic SDK `tools` + `tool_choice` so schema validation is enforced at the API boundary instead of via JSON repair + retry.
2. **Multi-location fix proposals (≈4h).** Today `FixProposal` is one anchor → one edit. Many real bugs (a date format mentioned in policy + tool section + example) need coordinated edits. Extend the schema to `edits: list[Edit]` and teach analysis to recognize multi-site issues.
3. **Richer multi-turn probe (≈3h).** Today's follow-up generator produces a single next message; a proper adversarial caller would adapt persona (escalating / disengaged / confused) and include a stop condition.
4. **Cost + token budgets (≈1h).** `--max-cost` flag that aborts gracefully before the next paid call once exceeded.
5. **Unit test suite (≈4h).** Anchor disambiguation, fuzzy matching, schema registry extraction, loader edge cases, KG round-trip, async probe flow — none currently have automated coverage.
6. **Non-healthcare regression prompts (≈2h).** Commit 2-3 synthetic prompts (fintech fraud check, SaaS cancel flow) and run them through CI to validate the domain-agnostic path stays domain-agnostic.
7. **LLM-as-judge calibration (≈half-day).** Seed a small annotated set of (original, fixed, ground-truth-score) triples and measure judge correlation; adjust rubric weight where it drifts.
8. **Post-apply seam smoother (≈2h).** Optional LLM pass after all fixes land that touches only grammar/tone at edit boundaries — no semantic changes.

## Agentic Patterns Used

| Pattern | Where |
|---|---|
| Prompt chaining | Each pass consumes structured output from the previous |
| Reflection | Detection self-critiques before proposals are generated |
| Plan-Execute | All fixes proposed before any are applied |
| Human-in-the-Loop | User picks fixes before anything is mutated |
| Evaluation / LLM-as-judge | Independent judge scores behavior deltas |
| Iteration + self-correction | `--iterate` re-analyzes failures with verdict feedback |
| Cross-run memory | Consolidated knowledge graph loaded into analysis, updated after verification |
| Parallel tool use | Independent probe traces execute concurrently within a bounded semaphore |
