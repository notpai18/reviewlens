"""FastAPI dependencies."""

from __future__ import annotations

from reviewlens.agent.factory import Components, build_components, make_context
from reviewlens.agent.nodes import AgentContext
from reviewlens.config import Settings, get_settings

_components: Components | None = None


def init_deps(settings: Settings) -> None:
    """Initialize persistent backend components (DuckDB, Qdrant, Gemini)."""
    global _components
    if _components is None:
        _components = build_components(settings)


def get_settings_dep() -> Settings:
    return get_settings()


def get_agent_ctx() -> AgentContext:
    """Dependency providing a per-request AgentContext with an isolated call budget."""
    settings = get_settings_dep()
    global _components
    if _components is None:
        _components = build_components(settings)
    return make_context(_components, settings)
