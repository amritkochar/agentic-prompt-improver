# Canonical Principles for Healthcare Voice-Agent Prompts

A curated library of quality principles the prompt-improver evaluates against.
Each entry has an ID, a one-sentence rule, rationale, a violation signature
(what a detector should look for), and the source that motivated it.

Principles are deliberately **healthcare voice-agent focused**. "When-applicable"
notes indicate principles that should only trigger when the input exhibits the
relevant signal (e.g. compliance/PHI, SMS templating).

---

## STRUCT — Prompt Structure & Cacheability

### STRUCT-01 — Static-first, variable-last
**Rule.** Put static, reusable content (role, policy, tool guidance, examples)
at the top of the prompt. Put variable or per-call state (current time, caller
number, slot IDs, patient context) at the bottom.
**Why.** LLM providers cache prompt prefixes. Intermixing variables early
breaks the cache and raises per-call cost and latency.
**Violation signature.** Variable placeholders ({{current_time}}, {{user_number}})
appearing mid-prompt rather than in a dedicated trailing context block; policy
text interleaved with per-call state.
**Source.** Anthropic prompt-caching docs; run3 observation on the Ava prompt.

### STRUCT-02 — Sectioned with explicit delimiters
**Rule.** Organise the prompt into named sections with markdown headers or XML
tags (e.g. `## Scheduling`, `<tools>`, `<policies>`). Do not rely on paragraph
breaks alone.
**Why.** Headers anchor the model's attention, reduce cross-contamination
between topics, and make later edits surgical.
**Violation signature.** Long contiguous paragraphs covering multiple
unrelated topics; lack of any section boundary markers.
**Source.** Anthropic prompting best-practices; OpenAI prompt-engineering
guide.

### STRUCT-03 — Single-purpose sections
**Rule.** Each section covers one concern (scheduling, cancellation, identity
verification, escalation). Do not mix rules from different flows inside one
block.
**Why.** Mixed sections produce contradictions and make it hard for the model
to retrieve the relevant rule.
**Violation signature.** Cancellation rules inside a scheduling paragraph;
empathy cues scattered through tool-use instructions.
**Source.** OpenAI prompt-engineering guide.

---

## ROLE — Role Definition & Scope

### ROLE-01 — Explicit scope and out-of-scope
**Rule.** State what the agent MUST do and what it MUST NOT do, with explicit
transfer targets for anything out of scope.
**Why.** Implicit scope invites hallucination. Stating "not a medical
professional — never give medical advice" is stronger than leaving it unstated.
**Violation signature.** No "MUST NOT" clause; no explicit list of transfer
destinations; scope defined only positively.
**Source.** Anthropic prompting best-practices; OpenAI guardrails guide.

### ROLE-02 — Compact interaction contract near the top
**Rule.** Near the top of the prompt, summarise the interaction style in one
short paragraph: modality, tone, brevity, turn-taking, follow-up discipline.
**Why.** Style cues drift when buried; a visible contract keeps every reply
consistent with voice-modality constraints.
**Violation signature.** Style rules scattered across the prompt; no
top-of-prompt behavioural summary; long structured instructions placed before
the one-line character definition.
**Source.** OpenAI GPT-5 prompt-guidance; run3 observation.

---

## TOOL — Tool-Use Discipline

### TOOL-01 — Conditional, never blanket, tool use
**Rule.** Phrase tool instructions as "use X when CONDITION". Never say
"always call X" or "always use X".
**Why.** Blanket "always use" wording causes unnecessary calls, breaks
degraded-mode behaviour, and has been flagged as an anti-pattern by Anthropic.
**Violation signature.** Sentences containing "always call", "always use",
"every time, use the tool", "on every turn".
**Source.** Anthropic prompting best-practices (explicit warning).

### TOOL-02 — Short, specific tool descriptions
**Rule.** Each tool's description should state what it does, when to use it,
and what it returns — in 1–3 sentences.
**Why.** Vague descriptions ("manages appointments") degrade tool selection.
Explicit return shape helps the model reason about downstream steps.
**Violation signature.** Descriptions over 80 words; missing return-shape
notes; descriptions that describe *how* rather than *when* to use the tool.
**Source.** Anthropic tool-use best-practices; OpenAI function-calling guide.

### TOOL-03 — Gather minimum-viable info before a tool call
**Rule.** Specify the minimum parameters the agent should collect before
invoking each tool. Do not call a tool speculatively before required fields
are known.
**Why.** Missing-parameter calls fail silently or produce wrong matches
(e.g. finding the wrong patient).
**Violation signature.** Tool-use instruction with no list of required fields
to collect first; "then call find_patient" without mentioning name + DOB
prerequisites.
**Source.** OpenAI prompt-engineering (pre-tool-call info gathering).

### TOOL-04 — Parameter formats must match tool schema exactly
**Rule.** When the prompt specifies parameter formats (date shape, ID
prefixes, enum values), they must match the tool's declared schema character
for character.
**Why.** A tool may accept YYYY-MM-DD while the prompt tells the agent to
pass DD-MM-YYYY. Silent mismatches are the most common workflow-adherence bug.
**Violation signature.** Prompt says "format the date as DD-MM-YYYY" while
the tool schema says `type: string, format: YYYY-MM-DD`; enum values in prose
that don't appear in the tool schema.
**Source.** Anthropic tool-use; run3 WA-01 observation.

### TOOL-05 — Confirm key fields back after success
**Rule.** After a tool call that changes state (book, cancel, confirm),
instruct the agent to confirm the key fields back to the caller in speech.
**Why.** Silent successes make callers unsure the action happened; confirming
key fields once catches misheard slots before they become a complaint.
**Violation signature.** book_appointment / cancel_appointment flow with no
instruction to repeat back provider + date + time + location + visit type.
**Source.** Voice-UX best practice; run3 partial coverage.

### TOOL-06 — On tool failure: explain, alternative, transfer
**Rule.** For every tool used, specify what the agent does when the call
fails, returns empty, or returns an error. The response must include a plain
explanation, a next-best alternative where possible, and a transfer target as
last resort.
**Why.** Unhandled tool failures cause the agent to either silently repeat
the call or fabricate success. Explicit failure paths are mandatory.
**Violation signature.** No "if the tool returns an error / no match / empty
list" clause for any of the state-changing tools.
**Source.** Anthropic tool-use best-practices; OpenAI guardrails guide;
run3 WA-17.

### TOOL-07 — Never reference non-existent tools or parameters
**Rule.** Every tool and parameter named in prose must exist in the tool
catalog. Likewise, every capability implied in prose (waitlist, message
provider, etc.) must have a backing tool.
**Why.** Referencing missing capabilities guarantees broken behaviour on the
real call — the agent has no way to carry out the instruction.
**Violation signature.** Instructions mention a "waitlist" action with no
tool named anything like `add_to_waitlist`; "send a message to the provider"
with no such tool.
**Source.** Run3 WA-07 observation.

---

## ELIG — Eligibility & Pre-Checks

### ELIG-01 — Validate preconditions before offering slots
**Rule.** Before calling slot-search or booking tools, validate all
eligibility rules relevant to the requested visit: age, sex, new-vs-returning,
referral, insurance, hours, visit-type compatibility, telehealth suitability.
**Why.** Offering a slot the patient is ineligible for wastes the call and
forces a painful walk-back. These checks must be a uniform pre-tool-call
contract, not scattered reminders.
**Violation signature.** Eligibility rules present in provider/visit-type
sections but no step in the scheduling flow that instructs "check all
applicable restrictions before get_available_slots".
**Source.** OpenAI optimizer output (eligibility & policy checks); run3.

### ELIG-02 — Policy-mismatch response is constructive
**Rule.** When a patient request fails an eligibility rule, the agent gives
one-sentence plain explanation plus the nearest valid alternative (different
provider, different visit type, referral path, transfer target).
**Why.** A flat "you cannot" without a path forward is the worst patient
experience case and causes abandoned calls.
**Violation signature.** Eligibility clauses that only say "do not schedule
X" without specifying what to offer instead.
**Source.** Run3 observation; voice-UX best practice.

---

## STYLE — Response Style for Modality

### STYLE-01 — Modality-appropriate length
**Rule.** Match reply length to modality. Voice and urgent calls use short,
transactional sentences (~1–2 short sentences per turn). Chat allows more
structure. SMS must be ultra-terse.
**Why.** Voice callers cannot skim; long replies cause interruption, lost
state, and frustration. Urgent callers need directive brevity.
**Violation signature.** Prompt allows multi-paragraph replies; no brevity
or single-focus rule for voice; instructions to "explain the policy" without
a length cap.
**Source.** OpenAI optimizer (response-style constraint); Anthropic voice
best-practices; run3 observation.

### STYLE-02 — Never dump policy text
**Rule.** Summarise only the rule that applies to the caller's specific
situation. Do not read out full policy lists, insurance tables, or
provider rosters.
**Why.** Reading a wall of policy text destroys the conversation. The agent
should act like a human receptionist: brief, directly responsive.
**Violation signature.** Instructions that imply reading accepted insurance
list in full; long enumerations of visit types read to every caller.
**Source.** Voice-UX best practice; run3 observation.

### STYLE-03 — Confirm key details once after success
**Rule.** After a successful state-changing action, read back the 5 key
fields once: provider, location, date, time, visit type — plus any prep.
Do not repeat them on every turn.
**Why.** One confirmation balances reassurance with brevity. Repeated
confirmations waste call time.
**Violation signature.** Either no post-action confirmation at all, or
instructions to re-confirm on every turn.
**Source.** Voice-UX best practice.

### STYLE-04 — Tone matches urgency
**Rule.** For emergency or distress cues (chest pain, breathing trouble,
suicidal ideation), use a short directive tone and the emergency handoff.
For routine calls, use a warmer register.
**Why.** Warm chit-chat during an emergency is harmful; directive tone on a
routine call is cold.
**Violation signature.** No emergency-handoff cue in the prompt; single tone
applied across all caller states.
**Source.** Anthropic prompting best-practices; voice-UX best practice.

---

## GUARD — Guardrails & Structured Outputs

### GUARD-01 — One targeted follow-up on ambiguity
**Rule.** When the caller's intent is ambiguous, ask ONE targeted follow-up
question at a time. Do not ask a list of questions in one turn.
**Why.** Stacked questions confuse voice callers, who can only hold one item
in working memory per turn.
**Violation signature.** Instructions like "ask for their first name, last
name, and date of birth" bundled into a single prompted turn.
**Source.** OpenAI optimizer (recovery/ambiguity rule); run3 PE-01.

### GUARD-02 — Function calling for actions and data access
**Rule.** Any action that changes state or fetches authoritative data MUST
go through a declared tool. The agent must not invent data (prices, slots,
patient records) conversationally.
**Why.** Conversational fabrication is the single biggest risk in a
healthcare context.
**Violation signature.** "Tell the patient what times are available" with
no `get_available_slots` path; fabricated prices or provider schedules
inlined into the prompt.
**Source.** OpenAI function-calling guide.

### GUARD-03 — Structured outputs only for machine consumers
**Rule.** Use structured outputs (JSON, templates) only when the consumer is
a machine — e.g. SMS template body, downstream system payload. Do not
require structured formatting for conversational speech.
**Why.** JSON-style replies read aloud sound robotic and break the voice
modality.
**Violation signature.** Instructions asking the voice agent to emit JSON
or bullet lists in spoken replies.
**Source.** OpenAI structured-outputs guide.

---

## CONTENT — Notification & Handoff Content

### CONTENT-01 — Notifications carry the 5-Ws
**Rule.** SMS / confirmation / notification bodies must include the
relevant 5-Ws: who (provider), what (visit type), when (date + time), where
(location), plus prep or warning as applicable.
**Why.** Partial SMS bodies ("your appointment is cancelled") leave the
patient guessing which appointment and force a call back.
**Violation signature.** `send_sms` instruction names a message type
(appointment_cancelled, appointment_confirmed) but does not require the
body to contain date, time, provider, location, or visit type.
**Source.** Run3 observation — the specific gap flagged by the user.

### CONTENT-02 — Preserve load-bearing context across handoffs
**Rule.** When transferring to a human or another flow, the agent must
pass (or recite) the load-bearing context: patient ID, verified identity,
reason for transfer, any steps already completed.
**Why.** Context loss on transfer forces the next agent to re-verify and
re-gather, making the caller repeat themselves.
**Violation signature.** `transfer_call` instructions with no
context-passing clause; bare transfer with only a target department name.
**Source.** Voice-UX best practice; run3 PE-09.

---

## SAFE — Safety & Atomicity

### SAFE-01 — Multi-step side-effect operations are atomic or rollback-aware
**Rule.** Multi-step operations with side effects (reschedule =
cancel + rebook) must either be atomic, or explicitly specify: do not
destroy old state until new state is confirmed, and include a rollback
path if the second step fails.
**Why.** The canonical failure is: cancel the old appointment, fail to find
a new slot, leave the patient with nothing. Explicit rollback discipline
prevents this.
**Violation signature.** Reschedule flow that cancels first without a
"hold" step, with no explicit "if no new slot found, keep the old
appointment" clause, and no rollback-on-failure instruction.
**Source.** Run3 WA-02 observation; software transactional safety pattern.

### SAFE-02 — Explicit preconditions before irreversible actions
**Rule.** Before an irreversible action (cancel without reschedule, send
SMS, transfer call), the agent must have verified identity and read back
the action for confirmation.
**Why.** Irreversible actions performed on an unverified or mis-heard
target are the worst real-world failure mode.
**Violation signature.** `cancel_appointment` invoked with no identity
verification step in the same flow; `send_sms` with no "confirm this is
correct" pause.
**Source.** OpenAI guardrails guide.

---

## CONSIST — Consistency

### CONSIST-01 — No contradictory policy text
**Rule.** Policy statements must not contradict each other across sections.
Resolve or remove any contradictions before shipping.
**Why.** The model will pick whichever contradiction it attended to most
strongly, producing inconsistent behaviour from call to call.
**Violation signature.** Two sections stating different rules for the
same situation (e.g. "transfer all new patients" vs. "describe new-patient
booking flow").
**Source.** Anthropic prompting best-practices; run3 WA-09.

### CONSIST-02 — Explicit precedence for overlapping rules
**Rule.** When two rules can both apply to the same situation, state which
wins (e.g. "emergency overrides the 48-hour advance rule",
"referral requirement overrides the same-practice exception").
**Why.** Unstated precedence forces the model to guess. Guessing is
unacceptable in a healthcare flow.
**Violation signature.** Pairs of rules that clearly overlap with no
"takes precedence" or "in this case" clause.
**Source.** OpenAI prompt-engineering guide.

---

## EX — Examples

### EX-01 — Examples diverse and used sparingly
**Rule.** Use short examples only where a policy is subtle or easy to
misread. Make them diverse — do not over-anchor on one pattern.
**Why.** Over-examples cause the model to copy the example's specific
values; under-examples leave ambiguity on the subtle cases.
**Violation signature.** Long lists of near-identical example dialogs;
subtle policies (telehealth eligibility, referral paths) with no
illustrative example at all.
**Source.** Anthropic prompting best-practices.

### EX-02 — Include negative examples where common failures exist
**Rule.** For failure modes observed in production or likely from common
misreads, include a brief "do NOT do X" example.
**Why.** Negative examples close off the most frequent wrong path more
reliably than positive examples alone.
**Violation signature.** Agent prompt lists positive examples only; no
"do not" illustration for the known foot-guns (e.g. "do not cancel
before confirming reschedule slot").
**Source.** OpenAI GPT-5 prompt-guidance.
