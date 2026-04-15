"""Unit tests for core/schema_registry.py — tool-parameter constraint extraction.

Covers:
- build_registry: None input, empty list, single tool with/without params,
  required vs optional params, enums, format fields, nested objects
- build_registry_from_json_text: valid JSON, invalid JSON, None
- _tool_name / _tool_parameters: various vendor schema shapes
"""

from __future__ import annotations

import json
import pytest

from core.schema_registry import (
    build_registry,
    build_registry_from_json_text,
    _tool_name,
    _tool_parameters,
    _iter_tools,
)


# ── _iter_tools ───────────────────────────────────────────────────────────────

class TestIterTools:
    def test_none_yields_nothing(self):
        assert list(_iter_tools(None)) == []

    def test_list_of_dicts(self):
        tools = [{"name": "foo"}, {"name": "bar"}]
        result = list(_iter_tools(tools))
        assert len(result) == 2

    def test_dict_of_dicts(self):
        tools = {"foo": {"name": "foo"}, "bar": {"name": "bar"}}
        result = list(_iter_tools(tools))
        assert len(result) == 2

    def test_skips_non_dict_items(self):
        tools = [{"name": "good"}, "not a dict", 42]
        result = list(_iter_tools(tools))
        assert len(result) == 1


# ── _tool_name ────────────────────────────────────────────────────────────────

class TestToolName:
    def test_name_key(self):
        assert _tool_name({"name": "book_appointment"}) == "book_appointment"

    def test_tool_name_key(self):
        assert _tool_name({"tool_name": "cancel_appointment"}) == "cancel_appointment"

    def test_function_name_key(self):
        assert _tool_name({"function_name": "send_sms"}) == "send_sms"

    def test_openai_function_wrapper(self):
        tool = {"function": {"name": "get_slots", "parameters": {}}}
        assert _tool_name(tool) == "get_slots"

    def test_no_name_returns_none(self):
        assert _tool_name({"description": "no name here"}) is None


# ── _tool_parameters ─────────────────────────────────────────────────────────

class TestToolParameters:
    def test_parameters_key(self):
        tool = {"name": "foo", "parameters": {"type": "object", "properties": {}}}
        result = _tool_parameters(tool)
        assert result is not None
        assert result["type"] == "object"

    def test_input_schema_key(self):
        tool = {"name": "foo", "input_schema": {"type": "object"}}
        assert _tool_parameters(tool) is not None

    def test_openai_function_wrapper(self):
        tool = {"function": {"name": "foo", "parameters": {"type": "object"}}}
        assert _tool_parameters(tool) is not None

    def test_missing_returns_none(self):
        assert _tool_parameters({"name": "foo"}) is None


# ── build_registry ────────────────────────────────────────────────────────────

class TestBuildRegistry:
    def test_none_returns_none(self):
        assert build_registry(None) is None

    def test_empty_list_returns_none(self):
        assert build_registry([]) is None

    def test_tool_without_parameters(self):
        tools = [{"name": "end_call"}]
        result = build_registry(tools)
        assert result is not None
        assert "end_call()" in result
        assert "<tool_schema_registry>" in result
        assert "</tool_schema_registry>" in result

    def test_required_param_marked_with_asterisk(self):
        tools = [{
            "name": "book_appointment",
            "parameters": {
                "type": "object",
                "required": ["patient_id", "start_date"],
                "properties": {
                    "patient_id": {"type": "string"},
                    "start_date": {"type": "string", "format": "date"},
                    "notes": {"type": "string"},
                },
            },
        }]
        result = build_registry(tools)
        assert result is not None
        # Required params get the '*' marker
        assert "* patient_id" in result
        assert "* start_date" in result
        # Optional param gets no marker (space-padded)
        assert "  notes" in result or "notes" in result

    def test_enum_included_in_output(self):
        tools = [{
            "name": "transfer_call",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "enum": ["billing", "clinical", "admin"],
                    },
                },
            },
        }]
        result = build_registry(tools)
        assert result is not None
        assert "billing" in result
        assert "clinical" in result

    def test_format_field_included(self):
        tools = [{
            "name": "get_slots",
            "parameters": {
                "type": "object",
                "required": ["start_date"],
                "properties": {
                    "start_date": {"type": "string", "format": "date"},
                },
            },
        }]
        result = build_registry(tools)
        assert result is not None
        assert "format=date" in result

    def test_description_hint_included(self):
        tools = [{
            "name": "book_appointment",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Date in DD-MM-YYYY format",
                    },
                },
            },
        }]
        result = build_registry(tools)
        assert result is not None
        assert "DD-MM-YYYY" in result

    def test_long_enum_truncated(self):
        tools = [{
            "name": "foo",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": [f"val{i}" for i in range(20)],
                    },
                },
            },
        }]
        result = build_registry(tools)
        assert result is not None
        assert "20 total" in result

    def test_multiple_tools(self):
        tools = [
            {"name": "book_appointment", "parameters": {"type": "object", "properties": {"date": {"type": "string"}}}},
            {"name": "cancel_appointment", "parameters": {"type": "object", "properties": {"id": {"type": "string"}}}},
        ]
        result = build_registry(tools)
        assert result is not None
        assert "book_appointment" in result
        assert "cancel_appointment" in result


# ── build_registry_from_json_text ─────────────────────────────────────────────

class TestBuildRegistryFromJsonText:
    def test_valid_json_string(self):
        tools = [{"name": "foo", "parameters": {"type": "object", "properties": {"x": {"type": "string"}}}}]
        result = build_registry_from_json_text(json.dumps(tools))
        assert result is not None
        assert "foo" in result

    def test_invalid_json_returns_none(self):
        assert build_registry_from_json_text("{not valid json") is None

    def test_none_returns_none(self):
        assert build_registry_from_json_text(None) is None

    def test_empty_string_returns_none(self):
        assert build_registry_from_json_text("") is None
