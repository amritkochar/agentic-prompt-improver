"""Core infrastructure package.

Shared data models, I/O utilities, and pipeline orchestration used by both
the CLI entry point (main.py) and the agents/ package.

Modules
-------
loader          Schema-agnostic JSON prompt loader
memory          Persistent cross-run knowledge graph (read/filter/write)
models          Pydantic data models for all inter-pass data
pipeline        Fix+verify loop and regression sweep orchestration
principles      Loads the canonical principles library text
reporting       Writes end-of-run artifacts (report.json, diff, fixed prompt)
schema_registry Deterministic extractor for tool-parameter constraints
ui              Rich terminal rendering and interactive selection
"""
