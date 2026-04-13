# Voice Agent Prompt Quality Tool

An agentic CLI tool that analyzes voice agent system prompts for quality issues, proposes targeted fixes, and **proves** each fix works via adversarial simulation and LLM-as-judge evaluation.

## Run

```bash
cd agent
pip3 install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python3 main.py ../Task\ +\ Solution\ Plan\ Files/assignment-agent-prompt.json
```

### Options

```bash
# Apply all fixes without prompting
python3 main.py prompt.json --auto-fix

# Detect issues only, no fixes or verification
python3 main.py prompt.json --dry-run

# Custom output directory
python3 main.py prompt.json --output-dir ./results/
```

## How It Works

**5-pass agentic pipeline:**

1. **Detection** — LLM analyzes prompt + tool definitions for issues across Patient Experience and Workflow Adherence
2. **Reflection** — LLM critiques its own findings (removes false positives, catches gaps)
3. **Analysis** — LLM proposes minimal, precise fixes with verbatim text diffs
4. **User Selection** — Interactive table; pick which issues to fix (or `--auto-fix`)
5. **Fix + Verify** — Apply fixes via exact string match (LLM fallback), then per-issue adversarial simulation with independent LLM judge scoring improvement 1-10

## Key Design Decisions

- **Detection includes reflection** — one extra LLM call filters false positives before they propagate into fix proposals
- **Adversarial scenarios are per-issue** — each test specifically targets one bug, not generic conversation simulation
- **LLM judge is separate from simulator** — prevents self-rationalization; judge never sees the system prompt
- **Schema-agnostic loader** — works on Vapi, Retell, Bland, ElevenLabs, or any custom JSON format
- **Exact string match first** — deterministic fixes when possible, LLM fallback only when needed
- **5 files, no framework** — simple composable pipeline, no orchestration overhead

## Agentic Patterns Used

| Pattern | Where | Source |
|---|---|---|
| Prompt Chaining | Each pass feeds the next | Gulli Ch. 1 |
| Reflection | Detection self-critiques | Gulli Ch. 4 |
| Plan-Execute | Detect + propose before applying | Gulli Ch. 6 |
| Human-in-the-Loop | User selection before changes | Gulli Ch. 13 |
| Evaluation | LLM-as-judge verification | Gulli Ch. 19 |

## Output

- `output/report.json` — full structured results: all issues, proposals, verdicts with scores
- `output/fixed_prompt.json` — original JSON with only the prompt text field modified

## What to Improve With More Time

- Multi-turn conversation simulation (currently single-turn adversarial)
- Parallel verification calls (currently sequential)
- Regression test suite: score same prompt across versions
- Healthcare-specific judge rubrics (HIPAA, clinical scope)

## AI Tools Used

Claude Sonnet 4.6 via Anthropic SDK for all agent passes; Claude Code for development.
