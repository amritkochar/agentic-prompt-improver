# Agentic Prompt Improver — Knowledge Graph

This file is maintained by the tool itself. Each run appends a log
entry and the consolidation pass merges observations into the
lessons + triples sections above. Feel free to read; edits will be
merged on the next run.

## Lessons (semantic layer, consolidated)

- [LSN-011] Exact_anchor method (structural assertion) is high-confidence for principle-based (non-behavioral) issues: TOOL-04, TOOL-06, ELIG-01, STRUCT-01, CONTENT-01, SAFE-02, CONSIST-02. All improved with score=8. (16/16 issues). Confidence: high |tags=method:exact_anchor, principles, non_behavioral|confidence=high|support=16|last_seen=2026-04-15
- [LSN-003] Fixing TOOL-04, TOOL-06, ELIG-01 (principles-based issues) via structural assertion validation (exact_anchor method) achieves improvement without behavioral probe. (3/3 runs). Confidence: high |tags=healthcare, TOOL-04, TOOL-06, ELIG-01, principles|confidence=high|support=3|last_seen=2026-04-15
- [LSN-004] WA-04 (eligibility pre-check annotation) shows improvement via explicit VIOLATION DETECTED labeling combined with decision-gate enforcement. Issue requires mandatory post-find_patient cert_period_end check blocking get_available_slots until recertification confirmed. (3/3 runs). Confidence: high |tags=healthcare, WA-04, confirmation_gate|confidence=high|support=3|last_seen=2026-04-15
- [LSN-005] Fixing WA-02 (reschedule atomic operation) via explicit preservation-of-original-appointment speech pattern and fallback-to-escalation path (not auto-retry) when booking fails. Requires prompt to enforce cancel-after-book ordering. Remains unchanged; fix incomplete as of run-2026-04-15. (2/3 runs). Confidence: medium |tags=healthcare, WA-02, atomic_operation|confidence=medium|support=3|last_seen=2026-04-15
- [LSN-001] Fixing WA-01 (modality constraint gating) via explicit pre-get_available_slots checklist with reordered questions (modality first) beats implicit conditional checking. Prevents patient from requesting unavailable modality. (2/2 runs). Confidence: high |tags=healthcare, WA-01, appointment_workflow|confidence=high|support=2|last_seen=2026-04-15
- [LSN-006] Fixing WA-03 (unavailability during leave) via explicit unavailability check + structured alternative routing ("Dr. Patel is unavailable due to maternity leave, Dr. Chen is excellent choice") beats ambiguous fallback speech. (2/2 runs). Confidence: high |tags=healthcare, WA-03, provider_unavailability|confidence=high|support=2|last_seen=2026-04-15
- [LSN-007] Fixing WA-01 (provider ID mapping) requires explicit 'Provider and Location ID Reference' table insertion into prompt, not just agent knowledge of mapping. Fixes tool-parameter guessing by enforcing structured ID lookup. (2/2 runs). Confidence: high |tags=healthcare, WA-01, tool_parameters|confidence=high|support=2|last_seen=2026-04-15
- [LSN-008] Fixing WA-05 (lab review ordering provider) via explicit fallback mapping (assigned_clinician → region → fallback clinician) embedded in CONDITIONS_CHECKED eliminates ambiguity. Removes '[Assuming...]' branching in probe. (2/2 runs). Confidence: high |tags=healthcare, WA-05, question_phrasing|confidence=high|support=2|last_seen=2026-04-15
- [LSN-009] Fixing WA-06 (appointment limit enforcement) requires find_appointment(status='scheduled') call *immediately after* identity verification, before asking appointment details. Prevents booking 4th appointment via tool-parameter correctness (clinician_id mapping). (2/2 runs). Confidence: high |tags=healthcare, WA-06, appointment_limit|confidence=high|support=2|last_seen=2026-04-15
- [LSN-002] Fixing WA-07 (provider-level modality override) via explicit pre-call constraint check on provider profile before invoking get_available_slots prevents tool calls that would return invalid slots. Probe inconclusive; original behavior already correct. (2/3 runs). Confidence: medium |tags=healthcare, WA-07, provider_constraints|confidence=medium|support=2|last_seen=2026-04-15
- [LSN-010] Fixing WA-07 (advance scheduling policy) validation logic already present in original; fix improves speech clarity and workflow certainty by explicit CONDITIONS_CHECKED sequencing, not behavioral change. Probe design matters. (2/3 runs). Confidence: medium |tags=healthcare, WA-07, probe_design|confidence=medium|support=2|last_seen=2026-04-15
- [LSN-012] Fixing WA-03 (caller identity verification for referrals) via consolidated single-question verification ('name and relationship') instead of sequential steps prevents bad-faith referrals and PHI exposure. Speech change operationalizes authorization check. (1/1 runs). Confidence: high |tags=healthcare, WA-03, referral_intake, verification|confidence=high|support=1|last_seen=2026-04-15
- [LSN-013] Fixing WA-06 (clinic coverage mapping) requires explicit rule citation in CONDITIONS_CHECKED: 'Diabetic education or ostomy care visits should be scheduled with the contract nursing pool.' Tool parameter change (clinician_id) from blanket David Osei to contract_rn_metro is the concrete fix. (1/1 runs). Confidence: high |tags=healthcare, WA-06, coverage_mapping|confidence=high|support=1|last_seen=2026-04-15
- [LSN-014] Fixing PE-01 (confirmation readback density) via splitting 1 long confirmation sentence into 2-3 short statements (who/what, when, where) per voice-interaction guidelines reduces cognitive load for elderly/stressed callers. (1/1 runs). Confidence: high |tags=healthcare, PE-01, voice_usability, accessibility|confidence=high|support=1|last_seen=2026-04-15
- [LSN-015] Fixing WA-09 (after-hours urgent escalation) requires concrete transfer_call destination ('on_call_nurse', not vague 'clinical_supervisor') mapped to urgent clinical issues. CONDITIONS_CHECKED must explicitly quote prompt rule mapping event-type → destination. (1/1 runs). Confidence: high |tags=healthcare, WA-09, after_hours, escalation|confidence=high|support=1|last_seen=2026-04-15
- [LSN-016] Fixing WA-10 (single-point-of-failure MSW routing) requires team-based transfer_call destination ('medical_social_work') not individual clinician name ('Felicia Brown'). Eliminates individual dependency and enables load balancing/fallback. (1/1 runs). Confidence: high |tags=healthcare, WA-10, team_routing, availability|confidence=high|support=1|last_seen=2026-04-15
- [LSN-017] Fixing WA-08 (clinician no-show documentation) requires: (1) detailed notes capturing family's specific report (waited, called, no answer, timing), (2) explicit workflow ordering rule in CONDITIONS_CHECKED (update_visit_status → transfer_call), (3) team destination ('scheduling_coordinator'). (1/1 runs). Confidence: high |tags=healthcare, WA-08, no_show_handling, documentation|confidence=high|support=1|last_seen=2026-04-15

## Triples (graph edges, with support counts)

| Head | Relation | Tail | Support | Last seen |
|---|---|---|---|---|
| verdict:improved | co_occurs_with | method:exact_anchor | 16 | 2026-04-15 |
| improvement_score:8 | co_occurs_with | verdict:improved | 16 | 2026-04-15 |
| method:exact_anchor | fuzzy_match_rate | 0.95 | 7 | 2026-04-15 |
| improvement_score:9 | co_occurs_with | verdict:improved | 4 | 2026-04-15 |
| principle:explicit_conditions_checked | leads_to | verdict:improved | 3 | 2026-04-15 |
| verdict:inconclusive | co_occurs_with | issue_pattern:WA-07 | 2 | 2026-04-15 |
| fix_strategy:explicit_pre_call_checklist | best_fixed_by | issue_pattern:WA-01 | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-01 | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-07 | 2 | 2026-04-15 |
| fix_strategy:provider_id_reference_table | best_fixed_by | issue_pattern:WA-01 | 2 | 2026-04-15 |
| fix_strategy:explicit_fallback_mapping | leads_to | verdict:improved | 2 | 2026-04-15 |
| issue_pattern:WA-05 | co_occurs_with | fix_strategy:explicit_fallback_mapping | 2 | 2026-04-15 |
| fix_strategy:find_appointment_post_verify | best_fixed_by | issue_pattern:WA-06 | 2 | 2026-04-15 |
| issue_pattern:WA-06 | co_occurs_with | fix_strategy:find_appointment_post_verify | 2 | 2026-04-15 |
| issue_pattern:WA-03 | co_occurs_with | fix_strategy:explicit_unavailability_routing | 2 | 2026-04-15 |
| fix_strategy:explicit_unavailability_routing | leads_to | verdict:improved | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-03 | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-04 | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-05 | 2 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-06 | 2 | 2026-04-15 |
| fix_strategy:confirmation_gate | leads_to | verdict:improved | 2 | 2026-04-15 |
| fix_strategy:provider_constraint_validation | best_fixed_by | issue_pattern:WA-07 | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | principle:pre_call_validation | 1 | 2026-04-15 |
| fix_strategy:explicit_reordering | leads_to | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:WA-04 | co_occurs_with | fix_strategy:confirmation_gate | 1 | 2026-04-15 |
| issue_pattern:WA-02 | co_occurs_with | fix_strategy:preserve_original_escalate | 1 | 2026-04-15 |
| issue_pattern:modality_constraint | best_fixed_by | fix_strategy:reorder_questions | 1 | 2026-04-15 |
| issue_pattern:provider_override | best_fixed_by | fix_strategy:provider_profile_check | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-02 | 1 | 2026-04-15 |
| improvement_score:2 | co_occurs_with | verdict:inconclusive | 1 | 2026-04-15 |
| verdict:unchanged | co_occurs_with | issue_pattern:WA-02 | 1 | 2026-04-15 |
| fix_strategy:preserve_original_escalate | leads_to | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:WA-03 | co_occurs_with | fix_strategy:consolidated_verification | 1 | 2026-04-15 |
| fix_strategy:consolidated_verification | leads_to | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:WA-06 | co_occurs_with | fix_strategy:coverage_rule_citation | 1 | 2026-04-15 |
| fix_strategy:coverage_rule_citation | leads_to | verdict:improved | 1 | 2026-04-15 |
| issue_pattern:PE-01 | co_occurs_with | fix_strategy:sentence_decomposition | 1 | 2026-04-15 |
| fix_strategy:sentence_decomposition | leads_to | verdict:improved | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:PE-01 | 1 | 2026-04-15 |
| issue_pattern:WA-09 | co_occurs_with | fix_strategy:concrete_destination_mapping | 1 | 2026-04-15 |
| fix_strategy:concrete_destination_mapping | leads_to | verdict:improved | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-09 | 1 | 2026-04-15 |
| issue_pattern:WA-10 | co_occurs_with | fix_strategy:team_based_routing | 1 | 2026-04-15 |
| fix_strategy:team_based_routing | leads_to | verdict:improved | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-10 | 1 | 2026-04-15 |
| issue_pattern:WA-08 | co_occurs_with | fix_strategy:detailed_notes_workflow_ordering | 1 | 2026-04-15 |
| fix_strategy:detailed_notes_workflow_ordering | leads_to | verdict:improved | 1 | 2026-04-15 |
| domain:healthcare | applies_in_domain | issue_pattern:WA-08 | 1 | 2026-04-15 |
| method:fuzzy_anchor | fuzzy_match_rate | 0.9121951219512195 | 1 | 2026-04-15 |

## Run log (episodic layer, last 20)

### run-2026-04-15T08:34 — carelink-agent-prompt.json (hash=682371e35a, domain=healthcare, modality=voice)
- issues=24, improved=22, unchanged=1, inconclusive=1, regressed=0

