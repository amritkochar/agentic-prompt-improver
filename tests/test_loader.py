"""Unit tests for core/loader.py — prompt loading and extraction.

Covers:
- load: JSON dict, JSON list, plain text, nested JSON
- extract_prompt_text: known keys preferred, longest-string fallback, raises on empty
- get_agent_name: known name keys, fallback
- get_prompt_key: returns correct key
"""

from __future__ import annotations

import json
import pytest

from core.loader import (
    load,
    extract_prompt_text,
    get_agent_name,
    get_prompt_key,
    KNOWN_KEYS,
)


# ── load ──────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_json_dict(self, tmp_path):
        data = {"general_prompt": "You are a helpful agent.", "name": "TestBot"}
        f = tmp_path / "prompt.json"
        f.write_text(json.dumps(data))
        result = load(str(f))
        assert result == data

    def test_json_list_wrapped(self, tmp_path):
        # A JSON array is wrapped under general_prompt
        data = ["item1", "item2"]
        f = tmp_path / "prompt.json"
        f.write_text(json.dumps(data))
        result = load(str(f))
        assert "general_prompt" in result

    def test_plain_text_wrapped(self, tmp_path):
        f = tmp_path / "prompt.txt"
        f.write_text("You are an agent that handles calls.")
        result = load(str(f))
        assert "general_prompt" in result
        assert "You are an agent" in result["general_prompt"]

    def test_invalid_json_treated_as_text(self, tmp_path):
        f = tmp_path / "prompt.json"
        f.write_text("{not valid json")
        result = load(str(f))
        assert "general_prompt" in result


# ── extract_prompt_text ───────────────────────────────────────────────────────

class TestExtractPromptText:
    LONG_TEXT = "A" * 200  # clearly longer than the 100-char threshold

    def test_prefers_known_key_in_order(self):
        # general_prompt is first in KNOWN_KEYS; should be selected over a longer value
        data = {
            "general_prompt": self.LONG_TEXT,
            "system_prompt": self.LONG_TEXT + "extra",
        }
        result = extract_prompt_text(data)
        assert result == self.LONG_TEXT

    def test_falls_back_to_longest_string(self):
        data = {
            "agent_name": "Bot",  # not a known key
            "description": self.LONG_TEXT,
        }
        result = extract_prompt_text(data)
        assert result == self.LONG_TEXT

    def test_short_known_key_skipped(self):
        # Known key exists but value is < 100 chars → fallback to longest
        data = {
            "general_prompt": "short",
            "body": self.LONG_TEXT,
        }
        result = extract_prompt_text(data)
        assert result == self.LONG_TEXT

    def test_raises_when_nothing_found(self):
        # All values are non-strings — no string value to extract
        with pytest.raises(ValueError, match="Could not find prompt text"):
            extract_prompt_text({"count": 42, "active": True, "items": [1, 2, 3]})

    def test_known_keys_order(self):
        # All KNOWN_KEYS present — should pick in declared order
        data = {k: self.LONG_TEXT + k for k in KNOWN_KEYS}
        result = extract_prompt_text(data)
        assert result == data[KNOWN_KEYS[0]]


# ── get_agent_name ────────────────────────────────────────────────────────────

class TestGetAgentName:
    def test_agent_name_key(self):
        assert get_agent_name({"agent_name": "Aria"}) == "Aria"

    def test_name_key(self):
        assert get_agent_name({"name": "Greenfield Bot"}) == "Greenfield Bot"

    def test_assistant_name_key(self):
        assert get_agent_name({"assistant_name": "CareBot"}) == "CareBot"

    def test_bot_name_key(self):
        assert get_agent_name({"bot_name": "SchedulerBot"}) == "SchedulerBot"

    def test_fallback_unknown(self):
        assert get_agent_name({"general_prompt": "..."}) == "Unknown Agent"

    def test_first_matching_key_wins(self):
        # agent_name should win over name
        result = get_agent_name({"agent_name": "First", "name": "Second"})
        assert result == "First"


# ── get_prompt_key ────────────────────────────────────────────────────────────

class TestGetPromptKey:
    LONG = "B" * 150

    def test_returns_known_key(self):
        data = {"general_prompt": self.LONG}
        assert get_prompt_key(data) == "general_prompt"

    def test_prefers_known_key_over_longer_unknown(self):
        data = {"general_prompt": self.LONG, "body": self.LONG + "extra"}
        assert get_prompt_key(data) == "general_prompt"

    def test_falls_back_to_longest_when_no_known_key(self):
        data = {"short_key": "x", "long_key": self.LONG}
        assert get_prompt_key(data) == "long_key"
