"""Thin Anthropic client wrapper with caching + per-model token stats.

`LLM.call_json` retries on parse failure and feeds the error back to the
model. `_cached_block` wraps static text for Anthropic prompt caching.
A module-level singleton `llm` is exposed so all passes share one stats
ledger and one client connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Union

import anthropic

logger = logging.getLogger(__name__)


SystemPrompt = Union[str, list[dict]]

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"


def _cached_block(text: str) -> dict:
    """Wrap static text as a cache_control ephemeral block for prompt caching."""
    return {
        "type": "text",
        "text": text,
        "cache_control": {"type": "ephemeral"},
    }


def _append_to_system(system: SystemPrompt, suffix: str) -> SystemPrompt:
    """Append a (non-cached) suffix to a system prompt without breaking the prefix cache."""
    if isinstance(system, str):
        return system + suffix
    return [*system, {"type": "text", "text": suffix}]


def _empty_model_stats() -> dict:
    return {
        "calls": 0,
        "input": 0,
        "cache_read": 0,
        "cache_create": 0,
        "output": 0,
    }


DEFAULT_CONCURRENCY = 5


class LLM:
    def __init__(self, model: str = SONNET_MODEL):
        self.client = anthropic.Anthropic()
        self._aclient: Optional[anthropic.AsyncAnthropic] = None
        self.model = model
        self.stats: dict[str, dict] = {}
        self._concurrency = DEFAULT_CONCURRENCY
        self._sem: Optional[asyncio.Semaphore] = None
        self._stats_lock: Optional[asyncio.Lock] = None

    # ── Concurrency control ────────────────────────────────────────────
    def set_concurrency(self, n: int) -> None:
        """Set the max number of concurrent async LLM calls. Must be called before any acall."""
        if n < 1:
            raise ValueError("concurrency must be >= 1")
        self._concurrency = n
        self._sem = None  # lazily re-initialized in the active event loop

    def _ensure_async_primitives(self) -> None:
        if self._aclient is None:
            self._aclient = anthropic.AsyncAnthropic()
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._concurrency)
        if self._stats_lock is None:
            self._stats_lock = asyncio.Lock()

    # ── Stats recording ────────────────────────────────────────────────
    def _record(self, model: str, usage) -> None:
        entry = self.stats.setdefault(model, _empty_model_stats())
        entry["calls"] += 1
        entry["input"] += getattr(usage, "input_tokens", 0) or 0
        entry["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        entry["cache_create"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        entry["output"] += getattr(usage, "output_tokens", 0) or 0

    async def _record_async(self, model: str, usage) -> None:
        assert self._stats_lock is not None
        async with self._stats_lock:
            self._record(model, usage)

    def snapshot(self) -> dict:
        return {m: dict(v) for m, v in self.stats.items()}

    @staticmethod
    def delta(after: dict, before: dict) -> dict:
        out: dict[str, dict] = {}
        for model in set(after) | set(before):
            a = after.get(model, _empty_model_stats())
            b = before.get(model, _empty_model_stats())
            row = {k: a.get(k, 0) - b.get(k, 0) for k in _empty_model_stats()}
            if row["calls"] > 0:
                out[model] = row
        return out

    def call(
        self,
        system: SystemPrompt,
        user: str,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> str:
        """Plain text response. Streams for large max_tokens to dodge SDK timeout."""
        model_id = model or self.model
        kwargs = {
            "model": model_id,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if max_tokens > 16000:
            with self.client.messages.stream(**kwargs) as stream:
                for _ in stream.text_stream:
                    pass
                final = stream.get_final_message()
            self._record(model_id, final.usage)
            return final.content[0].text

        response = self.client.messages.create(**kwargs)
        self._record(model_id, response.usage)
        return response.content[0].text

    def call_json(
        self,
        system: SystemPrompt,
        user: str,
        schema: type,
        max_tokens: int = 16000,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        """Structured JSON -> Pydantic model. Retries on parse failure."""
        json_instruction = (
            "\n\nYou MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside the JSON."
        )
        full_system = _append_to_system(system, json_instruction)

        last_err: Optional[Exception] = None
        current_user = user

        for attempt in range(max_retries + 1):
            raw = self.call(full_system, current_user, max_tokens, model=model)
            text = self._extract_json(raw)
            try:
                return schema.model_validate(json.loads(text))
            except Exception as err:
                last_err = err
                logger.warning(
                    "call_json parse failure (attempt %d/%d) for %s: %s",
                    attempt + 1, max_retries + 1, schema.__name__, err,
                )
                current_user = (
                    f"Your previous response could not be parsed as valid JSON matching the "
                    f"{schema.__name__} schema.\nError: {err}\n\n"
                    f"Return ONLY valid JSON. No markdown, no prose.\n\n"
                    f"Original request:\n{user}"
                )

        assert last_err is not None
        raise last_err

    # ── Async variants ─────────────────────────────────────────────────
    async def acall(
        self,
        system: SystemPrompt,
        user: str,
        max_tokens: int = 4096,
        model: Optional[str] = None,
    ) -> str:
        """Async mirror of `call`. Uses the shared concurrency semaphore."""
        self._ensure_async_primitives()
        assert self._aclient is not None
        assert self._sem is not None

        model_id = model or self.model
        kwargs = {
            "model": model_id,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }

        async with self._sem:
            try:
                if max_tokens > 16000:
                    async with self._aclient.messages.stream(**kwargs) as stream:
                        async for _ in stream.text_stream:
                            pass
                        final = await stream.get_final_message()
                    await self._record_async(model_id, final.usage)
                    return final.content[0].text

                response = await self._aclient.messages.create(**kwargs)
            except anthropic.RateLimitError as err:
                retry_after = float(getattr(err, "retry_after", 0) or 2.0)
                logger.warning("Rate-limited on %s — sleeping %.1fs before one retry", model_id, retry_after)
                await asyncio.sleep(retry_after)
                if max_tokens > 16000:
                    async with self._aclient.messages.stream(**kwargs) as stream:
                        async for _ in stream.text_stream:
                            pass
                        final = await stream.get_final_message()
                    await self._record_async(model_id, final.usage)
                    return final.content[0].text
                response = await self._aclient.messages.create(**kwargs)

            await self._record_async(model_id, response.usage)
            return response.content[0].text

    async def acall_json(
        self,
        system: SystemPrompt,
        user: str,
        schema: type,
        max_tokens: int = 16000,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        """Async mirror of `call_json`. Retries on parse failure."""
        json_instruction = (
            "\n\nYou MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside the JSON."
        )
        full_system = _append_to_system(system, json_instruction)

        last_err: Optional[Exception] = None
        current_user = user

        for attempt in range(max_retries + 1):
            raw = await self.acall(full_system, current_user, max_tokens, model=model)
            text = self._extract_json(raw)
            try:
                return schema.model_validate(json.loads(text))
            except Exception as err:
                last_err = err
                logger.warning(
                    "acall_json parse failure (attempt %d/%d) for %s: %s",
                    attempt + 1, max_retries + 1, schema.__name__, err,
                )
                current_user = (
                    f"Your previous response could not be parsed as valid JSON matching the "
                    f"{schema.__name__} schema.\nError: {err}\n\n"
                    f"Return ONLY valid JSON. No markdown, no prose.\n\n"
                    f"Original request:\n{user}"
                )

        assert last_err is not None
        raise last_err

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Best-effort isolation of the JSON blob in an LLM response."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        if not (text.startswith("{") and text.endswith("}")):
            first = text.find("{")
            last = text.rfind("}")
            if first != -1 and last > first:
                text = text[first:last + 1]
        return text


# Module-level singleton shared across every pass.
llm = LLM()
