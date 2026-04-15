"""Pass 0 — Establish Guiding Principles prompt."""

from __future__ import annotations

PRINCIPLES_INSTRUCTION = """\
You are a senior AI prompt engineer. You have been given a canonical library
of quality principles (above) and a specific conversational-agent system
prompt to evaluate (below).

Your job is to produce an ADAPTIVE BRIEF that focuses downstream passes on
the principles most load-bearing for THIS specific prompt. The tool was
originally tuned for healthcare voice agents, so be careful to stay
domain-agnostic — infer what this prompt actually is and let that drive your
selections, rather than assuming a booking workflow.

Your brief must contain:

1. modality — one of: voice, chat, sms, mixed, unknown.
   Infer from tool names (e.g. send_sms, transfer_call, phone-related tools
   indicate voice), the prompt's language ("phone call", "SMS", "chat"), and
   the overall interaction style. If ambiguous, default to "unknown".

2. domain — a single short slug identifying the primary domain. Examples:
   "healthcare", "fintech", "insurance", "customer_support",
   "telecom", "ecommerce", "travel", "scheduling", "unknown". Pick the single
   closest tag based on the prompt's vocabulary, tool set, and workflows.

3. domain_signals — 2-6 short tags adding nuance ("scheduling",
   "compliance", "phi", "fraud_check", "subscription_mgmt", etc.).

4. active_principles — principles this specific prompt VISIBLY VIOLATES or
   is at clear, concrete risk of violating given its domain and tool set.
   MAXIMUM 12. MINIMUM is whatever the evidence supports — 3 sharp entries
   beats 12 speculative ones. "Load-bearing" means: if this principle were
   violated, a production call would measurably break or degrade. Do NOT
   include a principle just because it is theoretically relevant; skip any
   that the prompt clearly already satisfies.

   For each entry:
   - id: the principle ID (e.g. "STYLE-01", "TOOL-04", "CONTENT-01")
   - reason: one short sentence naming the CONCRETE feature of the prompt
     that makes this principle load-bearing. Quote a fragment of the prompt
     or name a specific tool/section. Bad: "The prompt is a voice agent so
     STYLE-01 applies." Good: "Instruction 'Please confirm each of the
     following before proceeding: ...' lists 4 items in one voice reply —
     STYLE-01 violation risk."

5. interaction_contract — a single short paragraph (2–4 sentences) that
   summarises the expected interaction style for this agent given the
   detected modality and domain. Tailor wording to the domain (e.g. voice
   scheduling gets "brief transactional replies, one question at a time";
   chat fraud-check gets "confirm intent before destructive actions").

6. structure_notes — 1–2 sentences describing the prompt's current structure
   as it relates to cacheability and readability (e.g. is static policy kept
   separate from per-call variables? are there section boundaries? are tools
   listed by name or described in prose?).

IMPORTANT: be CONCRETE. "reason" fields must cite specific aspects of the
input prompt — not generic restatements of the principle.

Return ONLY valid JSON matching this schema:
{
  "modality": "voice",
  "domain": "healthcare",
  "domain_signals": ["healthcare", "scheduling"],
  "active_principles": [
    {"id": "STYLE-01", "reason": "Prompt contains multi-paragraph instructions for a voice call where long replies break the call"},
    ...
  ],
  "interaction_contract": "...",
  "structure_notes": "..."
}"""
