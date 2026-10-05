"""LLM client protocol definition."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from reviewlens.models import LLMResult, LLMStructured

T = TypeVar("T")


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM clients used by the ReviewLens agent."""

    async def generate_structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        temperature: float = 0.0,
    ) -> LLMStructured:
        """Generate a structured (JSON) response conforming to the given Pydantic schema."""
        ...

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        temperature: float = 0.0,
    ) -> LLMResult:
        """Generate a free-text response."""
        ...
