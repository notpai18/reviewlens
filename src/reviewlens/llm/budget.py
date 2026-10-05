"""Per-request LLM call budget guard (Section 9.6).

``BudgetedLLM`` wraps any ``LLMClient`` and refuses further calls once
``max_calls`` have been made. A fresh wrapper is created per agent request, so the
counter is naturally per request.
"""

from __future__ import annotations

from typing import Any, TypeVar

from reviewlens.models import LLMResult, LLMStructured

T = TypeVar("T")


class LLMBudgetExceeded(Exception):
    """Raised when a request tries to exceed AGENT_MAX_LLM_CALLS."""


class BudgetedLLM:
    """Delegating LLM client that enforces a maximum number of calls."""

    def __init__(self, inner: Any, max_calls: int) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self.calls = 0

    @property
    def inner(self) -> Any:
        return self._inner

    @property
    def remaining(self) -> int:
        return max(0, self._max_calls - self.calls)

    def _reserve(self) -> None:
        # Checked BEFORE the call, so the budget is never exceeded.
        if self.calls >= self._max_calls:
            raise LLMBudgetExceeded(f"LLM call budget of {self._max_calls} exhausted")
        self.calls += 1

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
    ) -> LLMStructured:
        self._reserve()
        result: LLMStructured = await self._inner.generate_structured(
            system=system, prompt=prompt, schema=schema, temperature=temperature
        )
        return result

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float = 0.0,
    ) -> LLMResult:
        self._reserve()
        result: LLMResult = await self._inner.generate_text(
            system=system, prompt=prompt, temperature=temperature
        )
        return result
