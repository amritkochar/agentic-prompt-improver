"""Deterministic extractor for tool-parameter constraints.

Given the `tools` / `general_tools` JSON from a loaded prompt, produce a compact
text registry of every declared parameter constraint — formats, enums, required
fields, types. Downstream detection receives this as structured context so the
LLM can cross-reference prose instructions against schemas without having to
re-parse the full JSON.

Kept vendor-agnostic: handles OpenAI-style function schemas, Vapi/Retell
variants, and plain {name, parameters} objects. Anything unrecognised is
silently skipped — the registry is best-effort.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional


def _iter_tools(tools: Any) -> Iterable[dict]:
    """Yield tool-definition dicts from whichever shape the input takes."""
    if tools is None:
        return
    if isinstance(tools, list):
        for item in tools:
            if isinstance(item, dict):
                yield item
    elif isinstance(tools, dict):
        # e.g. {"tool_name": {...}, ...}
        for value in tools.values():
            if isinstance(value, dict):
                yield value


def _tool_name(tool: dict) -> Optional[str]:
    for key in ("name", "tool_name", "function_name"):
        val = tool.get(key)
        if isinstance(val, str):
            return val
    fn = tool.get("function")
    if isinstance(fn, dict):
        name = fn.get("name")
        if isinstance(name, str):
            return name
    return None


def _tool_parameters(tool: dict) -> Optional[dict]:
    """Locate the JSON-Schema-ish parameters block."""
    for key in ("parameters", "input_schema", "schema"):
        val = tool.get(key)
        if isinstance(val, dict):
            return val
    fn = tool.get("function")
    if isinstance(fn, dict):
        for key in ("parameters", "input_schema"):
            val = fn.get(key)
            if isinstance(val, dict):
                return val
    return None


def _walk_properties(
    schema: dict,
    path: str = "",
) -> Iterable[tuple[str, dict]]:
    """Yield (dotted_path, property_schema) for every leaf-ish property."""
    props = schema.get("properties")
    if not isinstance(props, dict):
        return
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        current_path = f"{path}.{name}" if path else name
        yield current_path, prop
        # Recurse into nested objects so e.g. address.zip is surfaced.
        if prop.get("type") == "object" and isinstance(prop.get("properties"), dict):
            yield from _walk_properties(prop, current_path)
        if prop.get("type") == "array":
            items = prop.get("items")
            if isinstance(items, dict) and items.get("type") == "object":
                yield from _walk_properties(items, current_path + "[]")


def _constraint_line(path: str, prop: dict, required: bool) -> Optional[str]:
    """Return a one-line summary for a single parameter, or None if trivial."""
    bits: list[str] = []
    ptype = prop.get("type")
    if isinstance(ptype, str):
        bits.append(ptype)
    elif isinstance(ptype, list):
        bits.append("|".join(str(t) for t in ptype))

    fmt = prop.get("format")
    if isinstance(fmt, str):
        bits.append(f"format={fmt}")

    pattern = prop.get("pattern")
    if isinstance(pattern, str) and len(pattern) <= 80:
        bits.append(f"pattern={pattern}")

    enum = prop.get("enum")
    if isinstance(enum, list) and enum:
        # Truncate very long enums; keep first 10
        rendered = ", ".join(json.dumps(v) for v in enum[:10])
        if len(enum) > 10:
            rendered += f", … ({len(enum)} total)"
        bits.append(f"enum=[{rendered}]")

    desc = prop.get("description")
    if isinstance(desc, str) and desc.strip():
        # Look for format-like hints buried in free text. This is the bit
        # that catches prompts where the schema relies on a human-readable
        # description (common in voice-agent vendor configs).
        snippet = desc.strip().splitlines()[0][:120]
        bits.append(f'desc="{snippet}"')

    if not bits and not required:
        return None

    marker = "*" if required else " "
    return f"  {marker} {path}: {'; '.join(bits) if bits else '(no constraints)'}"


def build_registry(tools: Any) -> Optional[str]:
    """Produce the injectable text registry. Returns None if no tools present.

    Format:

        <tool_schema_registry>
        tool_name(param1, param2)
          * param1: string; format=date; desc="..."
            param2: string; enum=["a", "b"]
        other_tool(...)
          ...
        </tool_schema_registry>

    `*` marks required params. Intended to be short — one-line-per-param so a
    detection agent can scan it quickly.
    """
    lines: list[str] = []
    for tool in _iter_tools(tools):
        name = _tool_name(tool) or "(unnamed_tool)"
        params = _tool_parameters(tool)
        if not params:
            lines.append(f"{name}()")
            continue

        required = params.get("required") or []
        if not isinstance(required, list):
            required = []
        required_set = {r for r in required if isinstance(r, str)}

        leaves = list(_walk_properties(params))
        param_names = [path for path, _ in leaves if "." not in path and "[]" not in path]
        header = f"{name}({', '.join(param_names)})" if param_names else f"{name}()"
        lines.append(header)

        for path, prop in leaves:
            top_level = path.split(".", 1)[0].split("[]", 1)[0]
            line = _constraint_line(path, prop, top_level in required_set)
            if line:
                lines.append(line)

    if not lines:
        return None
    return "<tool_schema_registry>\n" + "\n".join(lines) + "\n</tool_schema_registry>"


def build_registry_from_json_text(tools_json: Optional[str]) -> Optional[str]:
    """Convenience wrapper: parses the JSON string form used in main.py."""
    if not tools_json:
        return None
    try:
        tools = json.loads(tools_json)
    except json.JSONDecodeError:
        return None
    return build_registry(tools)
