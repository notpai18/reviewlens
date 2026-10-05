"""Build the agent's runtime dependencies from settings.

Shared by the FastAPI app and the CLI so both wire things identically (model, rate
limit, timeouts, SQL caps and Qdrant credentials all come from ``Settings``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

from qdrant_client import QdrantClient

from reviewlens.agent.nodes import AgentContext
from reviewlens.config import Settings
from reviewlens.models import LLMResult, LLMStructured
from reviewlens.warehouse.catalog import load_meta_json
from reviewlens.warehouse.duckdb_backend import DuckDBBackend

T = TypeVar("T")


class LLMUnavailableError(Exception):
    """The LLM is not configured (e.g. GEMINI_API_KEY is missing)."""


class UnconfiguredLLM:
    """Placeholder client so the app can start (and serve /health) without an API key."""

    def __init__(self, reason: str = "GEMINI_API_KEY is not set") -> None:
        self._reason = reason

    async def generate_structured(
        self, *, system: str, prompt: str, schema: type[T], temperature: float = 0.0
    ) -> LLMStructured:
        raise LLMUnavailableError(self._reason)

    async def generate_text(
        self, *, system: str, prompt: str, temperature: float = 0.0
    ) -> LLMResult:
        raise LLMUnavailableError(self._reason)


@dataclass
class Components:
    llm: Any
    warehouse: DuckDBBackend
    qdrant: QdrantClient
    meta: dict[str, Any]


def build_llm(settings: Settings) -> Any:
    keys = settings.api_keys
    if not keys:
        return UnconfiguredLLM()
    from reviewlens.llm.gemini import GeminiLLM

    return GeminiLLM(
        api_key=keys,
        model=settings.gemini_model,
        rpm_limit=settings.llm_rpm_limit,
        timeout_s=settings.llm_timeout_s,
    )


def build_qdrant(settings: Settings) -> QdrantClient:
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        check_compatibility=False,
    )


def build_components(settings: Settings) -> Components:
    return Components(
        llm=build_llm(settings),
        warehouse=DuckDBBackend(
            settings.duckdb_path,
            max_rows=settings.sql_max_rows,
            timeout_s=settings.sql_timeout_s,
        ),
        qdrant=build_qdrant(settings),
        meta=load_meta_json(settings.data_meta_path),
    )


def make_context(components: Components, settings: Settings) -> AgentContext:
    """Create a per-request AgentContext (fresh LLM call budget)."""
    return AgentContext(
        llm=components.llm,
        warehouse=components.warehouse,
        qdrant_client=components.qdrant,
        meta=components.meta,
        settings=settings,
    )
