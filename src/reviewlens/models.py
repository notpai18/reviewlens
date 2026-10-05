"""Core data models for ReviewLens."""

from datetime import date as dt_date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Intent(StrEnum):
    """Classification of user intent."""

    ANALYTICS = "analytics"
    OUT_OF_SCOPE = "out_of_scope"
    UNSAFE_REQUEST = "unsafe_request"


class Tool(StrEnum):
    """Available tools for the agent."""

    SQL = "sql"
    DOCS = "docs"


class FindingKind(StrEnum):
    """Type of finding in the answer."""

    OBSERVED = "observed"
    PLAYER_FEEDBACK = "player_feedback"


class RetrievalMode(StrEnum):
    """Retrieval modes for search."""

    HYBRID_RRF = "hybrid_rrf"
    BM25 = "bm25"
    DENSE = "dense"


class Filters(BaseModel):
    """Search and query filters."""

    game: str | None = None
    rating_min: int | None = None
    rating_max: int | None = None
    date_from: dt_date | None = None
    date_to: dt_date | None = None
    app_versions: list[str] | None = None


class Plan(BaseModel):
    """Agent planning result."""

    intent: Intent
    standalone_question: str
    tools: list[Tool]
    sql_subquestion: str | None = None
    docs_query: str | None = None
    docs_depends_on_sql: bool = False
    filters: Filters = Field(default_factory=Filters)
    reason: str


class SQLResult(BaseModel):
    """Result of SQL execution."""

    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    error: str | None = None


class ValidationResult(BaseModel):
    """Result of SQL validation."""

    ok: bool
    normalized_sql: str | None = None
    reasons: list[str] = Field(default_factory=list)


class RetrievedReview(BaseModel):
    """A retrieved review from search."""

    review_id: str
    game: str
    review_date: dt_date
    rating: int
    app_version: str | None = None
    text: str
    score: float
    rank: int


class Finding(BaseModel):
    """A finding in the final answer."""

    statement: str
    evidence_ids: list[str]
    kind: FindingKind


class FinalAnswer(BaseModel):
    """Structured final answer."""

    summary: str = Field(..., description="2-3 sentences, direct answer first")
    findings: list[Finding] = Field(default_factory=list, max_length=5)
    caveats: list[str] = Field(default_factory=list)
    followups: list[str] = Field(default_factory=list, max_length=3)


class Citation(BaseModel):
    """Citation for evidence."""

    id: str
    type: Literal["sql", "review"]
    label: str | None = None
    snippet: str | None = None
    game: str | None = None
    date: dt_date | None = None
    rating: int | None = None


class TraceEvent(BaseModel):
    """Event in the agent trace."""

    node: str
    duration_ms: int
    summary: str


class Usage(BaseModel):
    """LLM usage tracking."""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class LLMResult(BaseModel):
    """Result from LLM call."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model: str


class LLMStructured(BaseModel):
    """Structured result from LLM call."""

    data: Any
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    model: str


class APIRequest(BaseModel):
    """Request to /ask endpoint."""

    question: str = Field(..., min_length=1, max_length=400)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=4)
    options: dict[str, Any] = Field(default_factory=dict)


class APIResponse(BaseModel):
    """Response from /ask endpoint."""

    request_id: str
    answer_markdown: str
    answer: FinalAnswer
    citations: list[Citation]
    sql: dict[str, Any] | None = None
    retrieved: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    cached: bool = False


class ErrorResponse(BaseModel):
    """Error response format."""

    error: dict[str, str]
