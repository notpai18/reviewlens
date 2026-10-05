"""Agent state definition."""

import operator
from typing import Annotated, TypedDict

from reviewlens.models import FinalAnswer, Plan, RetrievedReview, TraceEvent, Usage
from reviewlens.sql.generate import SQLToolResult


def add_usage(a: Usage, b: Usage) -> Usage:
    return Usage(
        llm_calls=a.llm_calls + b.llm_calls,
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        latency_ms=a.latency_ms + b.latency_ms,
    )


class AgentState(TypedDict, total=False):
    """State for the ReviewLens agent graph."""

    question: str
    history: list[dict[str, str]]

    plan: Plan | None
    sql_result: SQLToolResult | None
    docs_result: list[RetrievedReview] | None
    evidence: str | None

    final_answer: FinalAnswer | None
    refusal_reason: str | None

    trace: Annotated[list[TraceEvent], operator.add]
    usage: Annotated[Usage, add_usage]
    errors: Annotated[list[str], operator.add]
