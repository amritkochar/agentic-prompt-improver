"""Schema-agnostic prompt loader. Works with Vapi, Retell, Bland, ElevenLabs, or custom JSON."""

import json
from pathlib import Path

KNOWN_KEYS = ["general_prompt", "system_prompt", "prompt", "instructions", "content"]


def load(path: str) -> dict:
    """Load a prompt file. Handles JSON and plain text."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        return {"general_prompt": str(data)}
    except json.JSONDecodeError:
        return {"general_prompt": text}


def extract_prompt_text(data: dict) -> str:
    """Extract the main prompt text from loaded data. Tries known keys, falls back to longest string."""
    for key in KNOWN_KEYS:
        if key in data and isinstance(data[key], str) and len(data[key]) > 100:
            return data[key]

    longest = ""
    for value in data.values():
        if isinstance(value, str) and len(value) > len(longest):
            longest = value

    if longest:
        return longest

    raise ValueError("Could not find prompt text in the provided file.")


def get_prompt_key(data: dict) -> str:
    """Find which key holds the prompt text."""
    for key in KNOWN_KEYS:
        if key in data and isinstance(data[key], str) and len(data[key]) > 100:
            return key

    longest_key = ""
    longest_len = 0
    for key, value in data.items():
        if isinstance(value, str) and len(value) > longest_len:
            longest_key = key
            longest_len = len(value)
    return longest_key


def get_agent_name(data: dict) -> str:
    """Best-effort extraction of agent name."""
    for key in ["agent_name", "name", "assistant_name", "bot_name"]:
        if key in data and isinstance(data[key], str):
            return data[key]
    return "Unknown Agent"
