# Voice Agent Prompt Quality Tool

Agentic CLI that analyzes a conversational agent system prompt, detects real quality issues across caller experience and workflow adherence, proposes targeted fixes, and verifies each fix actually changes agent behavior via adversarial probe + independent LLM judge.

## Run

```bash
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python3 main.py docs/assignment-agent-prompt.json
```

### Options

```bash
python3 main.py PROMPT.json \
    [--auto-fix]          # apply all fixes without prompting
    [--dry-run]           # detect only, no fixes or verification
    [--iterate N]         # max analyze/apply/verify iterations (default 1)
    [--multi-turn N]      # caller turns per behavioral probe (default 1)
    [--output-dir DIR]    # default: output
    [-v]                  # verbose logging
```

## Outputs (in `output/`)

- `report.json` — full structured run: principles brief, detection, analysis, validations, verdicts, per-iteration metadata.
- `fixed_prompt.json` — original JSON with the prompt field replaced.
- `prompt.diff` — unified diff between original and final prompt text.

## How It Works

Six passes, each feeds the next; human-in-the-loop between detection and fixing.

| Pass | Purpose |
|---|---|
| 0. Establish Principles | Haiku selects ≤12 load-bearing canonical principles, infers modality + domain, builds a deterministic tool-schema registry from the tool definitions. |
| 1. Detection + Reflection | Sonnet flags issues across caller experience, workflow adherence, and principles violations; a second Sonnet call critiques the list and removes false positives / adds gaps. |
| 2. Fix Analysis | Sonnet proposes minimal fixes with a unique anchor, verifiable assertion, and mid-workflow behavioral probe. |
| 3. Fix Engine + Validation | Anchor-based text replacement with ambiguity rejection + fuzzy fallback + LLM fallback; each fix validated against its assertion. |
| 4. Behavioral Probe | For non-principles issues: single- or multi-turn probe simulated against both original and fixed prompt; independent judge scores 1-10. |
| 5. Regression Check | Previously-passing fixes are re-verified against the final prompt text; drops below threshold are flagged `regressed`. |

With `--iterate N`, failed fixes (score < 5) go back through passes 2-4 with verdict feedback up to N rounds before the regression sweep runs.

## Key Design Decisions

- **Structured tool-schema registry (`schema_registry.py`).** Parse parameter formats, enums, required fields, and description hints deterministically; inject them into detection so schema/prose mismatches (e.g. prompt says `DD-MM-YYYY`, tool schema says `date`) are caught systematically rather than relying on the LLM to notice.
- **Anchor disambiguation refuses to guess.** If an anchor appears in multiple places and `anchor_context` doesn't clearly pick a winner, the fix fails cleanly rather than silently landing in the wrong section. Fuzzy matches surface their similarity ratio to the user.
- **Judge stays channel-agnostic.** The simulator sees the system prompt; the judge sees only the two behaviors and the expected change, and uses a strict 1-10 rubric where only ≥7 counts as a behavioral pass. Partial improvements (4-6) are visible but don't flip the pass flag.
- **Iteration + regression.** Pipeline is a loop, not a line. Failing fixes re-enter analysis with the prior verdict as feedback; passing fixes are re-verified against the final text so overlapping edits can't silently regress earlier wins.
- **Domain-agnostic prompts, healthcare-tuned library.** Detection/reflection/analysis prompts are written in caller-neutral language; the canonical principles library is healthcare-flavored but fallbacks only add healthcare-specific principles when healthcare signals are actually present. A warning panel fires when a non-healthcare domain is inferred.
- **Prompt caching.** The canonical principles text is large and static, so it's wrapped as a single ephemeral cache block shared across passes. Typical cache-read ratios are 75-95%.
- **Minimal surface.** Five modules + one registry file. No agent framework, no DAG runner, no vector store.

## Cost & Performance

A typical run on the ~8K-token assignment prompt with default flags (`--iterate 1 --multi-turn 1`) touches ~5-7 LLM calls and costs under $0.30 at current Sonnet 4.6 pricing. `--iterate 3 --multi-turn 3` roughly quadruples that.

Model choice:
- **Haiku 4.5** for Pass 0 (cheap, short structured output).
- **Sonnet 4.6** everywhere else. Judge and analysis benefit from the extra reasoning; detection and reflection occasionally catch subtle bugs that Haiku misses.

## AI Tools Used

- **Claude Sonnet 4.6** / **Claude Haiku 4.5** via the Anthropic SDK for every agent pass; prompt caching via `cache_control` blocks.
- **Claude Code** (Sonnet 4.6) for the majority of the implementation — scaffolding, refactors, and review of the fix engine. Used in plan mode first to produce a critique + prioritized roadmap, then in execute mode to implement the changes against that plan.
- **OpenAI Prompt Optimizer** used during research to surface a handful of workflow/guardrail issues the baseline Detection prompt would have missed; those checks were folded into the detection rubric.

## What I'd Improve With More Time

Prioritized, with rough effort.

1. **Tool-use structured outputs (≈3h).** Migrate the critical `call_json` sites (detection, analysis, judge) to Anthropic SDK `tools` + `tool_choice` so schema validation is enforced at the API boundary instead of via JSON repair + retry.
2. **Async/parallel verification (≈2h).** Probes are independent per issue — current sequential loop is the longest wall-clock segment. `asyncio.gather` over verdicts.
3. **Richer multi-turn probe (≈3h).** Today's follow-up generator produces a single next message; a proper adversarial caller would adapt persona (escalating / disengaged / confused) and include a stop condition.
4. **Cost + token budgets (≈1h).** `--max-cost` flag that aborts gracefully before the next paid call once exceeded.
5. **Unit test suite (≈4h).** Anchor disambiguation, fuzzy matching, schema registry extraction, loader edge cases — none currently have automated coverage.
6. **Non-healthcare regression prompts (≈2h).** Commit 2-3 synthetic prompts (fintech fraud check, SaaS cancel flow) and run them through CI to validate the domain-agnostic path stays domain-agnostic.
7. **LLM-as-judge calibration (≈half-day).** Seed a small annotated set of (original, fixed, ground-truth-score) triples and measure judge correlation; adjust rubric weight where it drifts.

## Agentic Patterns Used

| Pattern | Where |
|---|---|
| Prompt chaining | Each pass consumes structured output from the previous |
| Reflection | Detection self-critiques before proposals are generated |
| Plan-Execute | All fixes proposed before any are applied |
| Human-in-the-Loop | User picks fixes before anything is mutated |
| Evaluation / LLM-as-judge | Independent judge scores behavior deltas |
| Iteration + self-correction | `--iterate` re-analyzes failures with verdict feedback |
