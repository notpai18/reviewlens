"""FakeLLM: scripted LLM client for tests.

Returns scripted responses — either an ordered list (consumed in sequence)
or (substring, response) matchers. Records all calls. Raises on unexpected calls.
All tests except llm/ use FakeLLM, never a real API.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from reviewlens.models import LLMResult, LLMStructured

T = TypeVar("T", bound=BaseModel)

_SENTINEL = object()


class FakeLLMError(Exception):
    """Raised when FakeLLM receives an unexpected call."""


class FakeLLM:
    """Scripted LLM client for testing.

    Usage:
        fake = FakeLLM(responses=["first answer", "second answer"])
        fake = FakeLLM(responses=[('crash', '{"sql": "SELECT ..."}'), ("fallback",)])

    responses items can be:
    - str: returned as text for generate_text or JSON-parsed for generate_structured
    - (substring: str, response: str): if substring is in the prompt, return response
    - A Pydantic BaseModel instance: serialized as JSON for generate_structured
    """

    def __init__(
        self,
        responses: list[Any] | None = None,
        default_response: str | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._default = default_response
        self._calls: list[dict[str, Any]] = []
        self._index = 0

    # -----------------------------------------------------------------------
    # Introspection helpers
    # -----------------------------------------------------------------------

    @property
    def calls(self) -> list[dict[str, Any]]:
        return list(self._calls)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    def reset(self) -> None:
        self._calls.clear()
        self._index = 0

    # -----------------------------------------------------------------------
    # Response resolution
    # -----------------------------------------------------------------------

    def _resolve(self, system: str, prompt: str) -> str:
        full = system + "\n" + prompt

        # Try ordered list first
        if self._index < len(self._responses):
            item = self._responses[self._index]
            self._index += 1

            if isinstance(item, tuple):
                substring, response = item
                if substring and substring not in full:
                    raise FakeLLMError(
                        f"FakeLLM matcher '{substring}' not found in prompt. "
                        f"Prompt preview: {full[:200]!r}"
                    )
                return response if isinstance(response, str) else response.model_dump_json()

            if isinstance(item, BaseModel):
                return item.model_dump_json()

            return str(item)

        if self._default is not None:
            return self._default

        raise FakeLLMError(
            f"FakeLLM ran out of scripted responses at call #{self._index + 1}. "
            "Add more responses or set default_response."
        )

    # -----------------------------------------------------------------------
    # Protocol implementation
    # -----------------------------------------------------------------------

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
    ) -> LLMStructured:
        self._calls.append({"type": "structured", "system": system, "prompt": prompt})
        raw = self._resolve(system, prompt)
        # Strip code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = schema.model_validate_json(raw)
        return LLMStructured(
            data=data,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(raw) // 4,
            latency_ms=0,
            model="fake",
        )

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float = 0.0,
    ) -> LLMResult:
        self._calls.append({"type": "text", "system": system, "prompt": prompt})
        raw = self._resolve(system, prompt)
        return LLMResult(
            text=raw,
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(raw) // 4,
            latency_ms=0,
            model="fake",
        )
