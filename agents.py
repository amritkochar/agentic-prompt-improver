"""All LLM agent passes: detect, reflect, analyze, fix, verify."""

from __future__ import annotations

import json
from typing import Optional

import anthropic

from models import (
    AnalysisResult,
    DetectionResult,
    FixProposal,
    Issue,
    JudgeRaw,
    JudgeVerdict,
    ScenarioResult,
)
from loader import KNOWN_KEYS


# ---------------------------------------------------------------------------
# LLM wrapper
# ---------------------------------------------------------------------------

class LLM:
    def __init__(self, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic()
        self.model = model

    def call(self, system: str, user: str, max_tokens: int = 4096) -> str:
        """Plain text response."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    def call_json(self, system: str, user: str, schema: type, max_tokens: int = 8192):
        """Structured JSON -> Pydantic model. Retries once on parse failure."""
        json_instruction = (
            "\n\nYou MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside the JSON."
        )
        full_system = system + json_instruction

        text = self._extract_json(self.call(full_system, user, max_tokens))

        try:
            return schema.model_validate(json.loads(text))
        except (json.JSONDecodeError, Exception) as first_err:
            # Retry once with error feedback
            retry_user = (
                f"Your previous response was not valid JSON.\n"
                f"Error: {first_err}\n\n"
                f"Please return ONLY valid JSON matching the required schema.\n\n"
                f"Original request:\n{user}"
            )
            text2 = self._extract_json(self.call(full_system, retry_user, max_tokens))
            return schema.model_validate(json.loads(text2))

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Strip markdown code fences if the model wrapped its JSON."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]  # drop opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        return text


llm = LLM()


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------

DETECTION_PROMPT = """\
You are an expert AI prompt engineer specializing in voice agent quality assurance.
Analyze the voice agent system prompt for quality issues across two dimensions:

PATIENT EXPERIENCE — issues causing robotic tone, inadequate empathy, unnatural speech,
poor de-escalation, missing emotional handling for distressed/angry/confused callers,
insufficient personality definition, phone-inappropriate verbosity.

WORKFLOW ADHERENCE — issues causing incorrect tool behavior: parameter format mismatches
between different tools, missing error handling, dangerous operation ordering (e.g. cancel
before rebook with no rollback), business rule contradictions, missing guard conditions
before irreversible actions, ambiguous decision trees, missing workflow paths, instructions
referencing capabilities that don't exist in the tool definitions.

IMPORTANT: You must also analyze the tool definitions (parameters, types, formats) and
cross-reference them against the prompt instructions. Look for format mismatches between
tools, missing parameters in instructions, and instructions that reference tools or
capabilities that don't exist.

For each issue:
- Quote the EXACT text from the prompt as evidence (copy-paste verbatim)
- Rate severity: critical (breaks behavior silently), high (user-facing problem), \
medium (quality degradation), low (minor improvement)
- Assign an ID: WA-XX for workflow adherence, PE-XX for patient experience
- Only report genuine issues that would affect production calls, not stylistic preferences

Return JSON matching this schema:
{
  "issues": [
    {
      "id": "WA-01",
      "dimension": "workflow_adherence",
      "severity": "critical",
      "title": "Short descriptive title",
      "description": "What the problem is and why it matters in production",
      "evidence": "Verbatim quote from the prompt proving the issue exists",
      "location_hint": "Which section of the prompt"
    }
  ],
  "analysis_notes": "Brief summary of analysis approach and confidence level"
}"""

REFLECTION_PROMPT = """\
You are reviewing a list of quality issues found in a voice agent prompt.
Your job is to critique this list rigorously.

REMOVE any issues that are:
- False positives (the prompt actually handles it correctly elsewhere — check carefully)
- Stylistic preferences rather than real quality problems
- Duplicates of other issues in the list
- Too vague or speculative to be actionable

ADD any obvious high-severity issues that were missed, especially:
- Tool parameter format mismatches between different tools
- Missing instructions for tool failure/error scenarios
- Dangerous operation ordering (irreversible actions without safety checks)
- Business rules stated but unenforceable with available tools
- Missing workflow paths for common caller scenarios

For each issue you keep or add, verify the evidence quote is accurate.

Return the refined JSON with the same schema:
{
  "issues": [...],
  "analysis_notes": "What you changed and why"
}"""

ANALYSIS_PROMPT = """\
You are an expert AI prompt engineer proposing fixes for identified quality issues \
in a voice agent prompt.

Rules for fixes:
- MINIMAL: change as few words as possible. Do not rewrite sections unnecessarily.
- PRECISE: original_text must be a VERBATIM substring that exists in the prompt \
(copy-paste exact match). If you cannot find an exact substring, use is_addition=true.
- CORRECT: the fix must resolve the issue without creating new problems
- CONSISTENT: fixes for related issues must not contradict each other
- PRACTICAL: fixes should work within the constraints of the existing tool definitions

For replacement fixes (is_addition=false):
- original_text: the exact substring from the prompt to replace
- replacement_text: what to replace it with

For addition fixes (is_addition=true):
- insertion_after: the exact text in the prompt to insert after
- replacement_text: the new text to add
- original_text: set to empty string ""

Return JSON matching this schema:
{
  "proposals": [
    {
      "issue_id": "WA-01",
      "root_cause": "Why this issue exists",
      "impact_if_unfixed": "What goes wrong in production",
      "fix_description": "What the fix does",
      "original_text": "exact verbatim text to replace",
      "replacement_text": "the replacement",
      "is_addition": false,
      "insertion_after": null
    }
  ]
}"""

ADVERSARIAL_PROMPT = """\
You are generating a test scenario for a voice agent quality issue.

Write a realistic caller message that would specifically trigger the issue described
when the original (unfixed) prompt is used as the agent's system prompt.

The message must be:
- Something a real patient would plausibly say on the phone
- Natural language, not robotic or contrived
- Targeted enough that the specific issue would cause the agent to behave incorrectly
- Short enough for a single conversational turn (1-3 sentences)

Return JSON:
{
  "scenario_description": "Brief description of the test scenario",
  "user_message": "The exact caller message to test with",
  "why_adversarial": "Why this message specifically triggers this issue"
}"""

SIMULATOR_PROMPT = """\
You are simulating a voice agent. You MUST respond to the caller's message EXACTLY as the
agent described in the system prompt would respond — including reproducing any issues or
limitations in the prompt authentically.

IMPORTANT:
- Do NOT correct problems you notice in the system prompt
- Do NOT add capabilities the prompt doesn't give you
- Respond naturally as the agent would on a phone call
- If the prompt has gaps or ambiguities, respond how a literal reading of the prompt \
would lead you to respond

After your response, on a new line add:
TRIGGERED_ISSUE: [yes/no] - [one sentence explaining whether the issue manifested]"""

JUDGE_PROMPT = """\
You are an objective, skeptical evaluator comparing two voice agent responses to \
the same caller message.

An improvement ONLY counts if:
1. The specific problem behavior actually changed (not just rewording the same mistake)
2. The fix addresses the root cause, not just symptoms
3. No new problems were introduced by the fix
4. The response is still natural and appropriate for a phone call

Score on a 1-10 scale:
- 1-3: No meaningful improvement — same problem persists or fix is cosmetic only
- 4-6: Partial improvement — problem reduced but not fully resolved
- 7-9: Clear improvement — the specific issue is resolved and response is better
- 10: Complete resolution — issue fully fixed, no remaining concerns

Be skeptical. Default to lower scores unless the improvement is clearly demonstrated.

Return JSON:
{
  "improvement_detected": true,
  "improvement_score": 7,
  "explanation": "Detailed explanation of what changed and whether it matters",
  "remaining_concerns": "Any remaining issues, or null if fully resolved"
}"""


# ---------------------------------------------------------------------------
# Pass 1: Detection + Reflection
# ---------------------------------------------------------------------------

def detect(prompt_text: str, tools_json: Optional[str] = None) -> DetectionResult:
    """Detect quality issues in the prompt, then self-critique via reflection."""
    user_content = f"<prompt>\n{prompt_text}\n</prompt>"
    if tools_json:
        user_content += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"

    # Detection pass
    raw = llm.call_json(DETECTION_PROMPT, user_content, DetectionResult)

    # Reflection pass — critique and refine
    reflection_input = (
        f"<detected_issues>\n{raw.model_dump_json(indent=2)}\n</detected_issues>"
        f"\n\n<original_prompt>\n{prompt_text}\n</original_prompt>"
    )
    if tools_json:
        reflection_input += f"\n\n<tool_definitions>\n{tools_json}\n</tool_definitions>"

    refined = llm.call_json(REFLECTION_PROMPT, reflection_input, DetectionResult)
    return refined


# ---------------------------------------------------------------------------
# Pass 2: Analysis
# ---------------------------------------------------------------------------

def analyze(prompt_text: str, issues: list[Issue]) -> AnalysisResult:
    """Propose minimal, precise fixes for each detected issue."""
    issues_json = json.dumps([i.model_dump() for i in issues], indent=2)
    user_content = (
        f"<prompt>\n{prompt_text}\n</prompt>"
        f"\n\n<issues>\n{issues_json}\n</issues>"
    )
    return llm.call_json(ANALYSIS_PROMPT, user_content, AnalysisResult)


# ---------------------------------------------------------------------------
# Pass 3: Fix Engine
# ---------------------------------------------------------------------------

def _llm_assisted_fix(text: str, proposal: FixProposal) -> str:
    """Fallback: use LLM to apply a fix when exact string match fails."""
    system = (
        "You are a precise text editor. Apply the described fix to the prompt text. "
        "Return ONLY the complete modified prompt text. No explanations, no markup."
    )
    user = (
        f"Apply this fix to the prompt:\n\n"
        f"Fix: {proposal.fix_description}\n"
        f"Text to find (approximate): {proposal.original_text}\n"
        f"Replace with: {proposal.replacement_text}\n\n"
        f"Full prompt to modify:\n{text}"
    )
    return llm.call(system, user, max_tokens=16384)


def apply_fixes(
    original_json: dict,
    prompt_text: str,
    proposals: list[FixProposal],
    selected_ids: list[str],
) -> tuple[dict, str, list[str]]:
    """Apply selected fixes. Returns (fixed_json, fixed_text, applied_ids)."""
    selected = [p for p in proposals if p.issue_id in selected_ids]
    text = prompt_text
    applied: list[str] = []

    for proposal in selected:
        if proposal.is_addition:
            if proposal.insertion_after and proposal.insertion_after in text:
                idx = text.find(proposal.insertion_after)
                insert_pos = idx + len(proposal.insertion_after)
                text = text[:insert_pos] + "\n" + proposal.replacement_text + text[insert_pos:]
                applied.append(proposal.issue_id)
            else:
                new_text = _llm_assisted_fix(text, proposal)
                if new_text and len(new_text) > len(text) * 0.5:
                    text = new_text
                    applied.append(proposal.issue_id)
        else:
            if proposal.original_text and proposal.original_text in text:
                text = text.replace(proposal.original_text, proposal.replacement_text, 1)
                applied.append(proposal.issue_id)
            else:
                new_text = _llm_assisted_fix(text, proposal)
                if new_text and len(new_text) > len(text) * 0.5:
                    text = new_text
                    applied.append(proposal.issue_id)

    # Update the prompt field in the JSON
    fixed_json = dict(original_json)
    prompt_key = None
    for key in KNOWN_KEYS:
        if key in fixed_json:
            prompt_key = key
            break
    if not prompt_key:
        longest_len = 0
        for key, value in fixed_json.items():
            if isinstance(value, str) and len(value) > longest_len:
                prompt_key = key
                longest_len = len(value)
    if prompt_key:
        fixed_json[prompt_key] = text

    return fixed_json, text, applied


# ---------------------------------------------------------------------------
# Pass 4: Verification
# ---------------------------------------------------------------------------

def verify(
    issue_id: str,
    proposal: FixProposal,
    original_prompt: str,
    fixed_prompt: str,
) -> JudgeVerdict:
    """Verify a single fix via adversarial simulation + LLM-as-judge."""

    # Step 1 — Generate adversarial scenario
    scenario_input = json.dumps(
        {
            "issue_id": issue_id,
            "title": proposal.fix_description,
            "root_cause": proposal.root_cause,
            "impact": proposal.impact_if_unfixed,
        },
        indent=2,
    )
    scenario = llm.call_json(ADVERSARIAL_PROMPT, scenario_input, ScenarioResult)

    # Step 2 — Simulate original agent response
    orig_response = llm.call(original_prompt, scenario.user_message)

    # Step 3 — Simulate fixed agent response
    fixed_response = llm.call(fixed_prompt, scenario.user_message)

    # Step 4 — Independent judge
    judge_input = json.dumps(
        {
            "issue": {
                "id": issue_id,
                "fix_description": proposal.fix_description,
                "root_cause": proposal.root_cause,
                "impact_if_unfixed": proposal.impact_if_unfixed,
            },
            "scenario": scenario.scenario_description,
            "caller_message": scenario.user_message,
            "original_agent_response": orig_response,
            "fixed_agent_response": fixed_response,
        },
        indent=2,
    )
    verdict_data = llm.call_json(JUDGE_PROMPT, judge_input, JudgeRaw)

    return JudgeVerdict(
        issue_id=issue_id,
        scenario_description=scenario.scenario_description,
        user_message=scenario.user_message,
        original_response=orig_response,
        fixed_response=fixed_response,
        improvement_detected=verdict_data.improvement_detected,
        improvement_score=verdict_data.improvement_score,
        explanation=verdict_data.explanation,
        remaining_concerns=verdict_data.remaining_concerns,
    )
