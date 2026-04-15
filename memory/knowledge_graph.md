# Agentic Prompt Improver — Knowledge Graph

This file is maintained by the tool itself. Each run appends a log
entry and the consolidation pass merges observations into the
lessons + triples sections above. Feel free to read; edits will be
merged on the next run.

## Lessons (semantic layer, consolidated)

- [LSN-001] Fixing WA-01 (modality constraint gating) via explicit pre-get_available_slots checklist with reordered questions (modality first) beats implicit conditional checking. Prevents patient from requesting unavailable modality. (1/1 runs). Confidence: high |tags=healthcare, WA-01, appointment_workflow|confidence=high|support=1|last_seen=2026-04-15
- [LSN-002] Fixing WA-07 (provider-level modality override) via explicit pre-call constraint check on provider profile before invoking get_available_slots prevents tool calls that would return invalid slots. (1/1 runs). Confidence: high |tags=healthcare, WA-07, provider_constraints|confidence=high|support=1|last_seen=2026-04-15
- [LSN-003] Fixing TOOL-04, TOOL-06, ELIG-01 (principles-based issues) via structural assertion validation (exact_anchor method) achieves improvement without behavioral probe. (1/1 runs). Confidence: high |tags=healthcare, TOOL-04, TOOL-06, ELIG-01, principles|confidence=high|support=1|last_seen=2026-04-15
- [LSN-004] WA-04 (eligibility pre-check annotation) shows improvement via explicit VIOLATION DETECTED labeling, but original behavior already enforced constraint correctly. Probe design limitation: scenario does not force agent to skip eligibility check without annotation. (1/1 runs). Confidence: medium |tags=healthcare, WA-04, probe_design, inconclusive|confidence=medium|support=1|last_seen=2026-04-15
- [LSN-005] WA-02 (reschedule atomic operation) fix requires probe that exercises booking failure or slot unavailability to validate rollback logic. Current scenario does not demonstrate cancel-then-book ordering or rollback behavior. (1/1 runs). Confidence: medium |tags=healthcare, WA-02, probe_design, inconclusive|confidence=medium|support=1|last_seen=2026-04-15

## Triples (graph edges, with support counts)

| Head | Relation | Tail | Support | Last seen |
|---|---|---|---|---|
| method:exact_anchor | fuzzy_match_rate | 0.95 | 7 | 2026-04-15 |
| verdict:improved | co_occurs_with | method:exact_anchor | 4 | 2026-04-15 |
| improvement_score:8 | co_occurs_with | verdict:improved | 3 | 2026-04-15 |
| verdict:inconclusive | co_occurs_with | issue_pattern:rescue_scenario_gap | 2 | 2026-04-15 |
| fix_strategy:explicit_pre_call_checklist | best_fixed_by | issue_pattern:WA-01 | 1 | 2026-04-15 |
| fix_strategy:provider_constraint_validation | best_fixed_by | issue_pattern:WA-07 | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | principle:pre_call_validation | 1 | 2026-04-15 |
| fix_strategy:explicit_reordering | leads_to | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:WA-04 | co_occurs_with | behavioral_issue:constraint_already_enforced | 1 | 2026-04-15 |
| issue_pattern:WA-02 | co_occurs_with | behavioral_issue:atomic_operation_ordering | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-01 | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-07 | 1 | 2026-04-15 |
| improvement_score:9 | co_occurs_with | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:modality_constraint | best_fixed_by | fix_strategy:reorder_questions | 1 | 2026-04-15 |
| issue_pattern:provider_override | best_fixed_by | fix_strategy:provider_profile_check | 1 | 2026-04-15 |

## Run log (episodic layer, last 20)

### run-2026-04-15T07:15 — assignment-agent-prompt.json (hash=15cdabe3b8, domain=healthcare, modality=voice)
- issues=22, improved=5, unchanged=0, inconclusive=2, regressed=0

