"""API request and response schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from reviewlens.models import FinalAnswer, TraceEvent, Usage


class Message(BaseModel):
    role: str
    content: str


class AskOptions(BaseModel):
    include_trace: bool = False


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=400)
    history: list[Message] = Field(default_factory=list, max_length=4)
    options: AskOptions = Field(default_factory=AskOptions)


class CitationSchema(BaseModel):
    id: str
    type: Literal["sql", "review"]
    label: str | None = None
    snippet: str | None = None
    game: str | None = None
    date: str | None = None
    rating: int | None = None


class SQLInfo(BaseModel):
    query: str
    status: str
    attempts: int
    columns: list[str]
    rows_preview: list[list[Any]]


class RetrievedInfo(BaseModel):
    """A retrieved review that no finding cites."""

    review_id: str
    rank_score: float = 0.0
    snippet: str
    game: str | None = None
    date: str | None = None
    rating: int | None = None


class AskResponse(BaseModel):
    request_id: str
    answer_markdown: str
    answer: FinalAnswer | None
    citations: list[CitationSchema]
    sql: list[SQLInfo]
    retrieved: list[RetrievedInfo]
    trace: list[TraceEvent] | None
    usage: Usage | None
    cached: bool


class ErrorDetail(BaseModel):
    code: str | int
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class IngestRequest(BaseModel):
    mode: Literal["rebuild"] = "rebuild"


class IngestStatusResponse(BaseModel):
    status: Literal["idle", "running", "completed", "failed"]
    indexed_count: int | None = None
    error: str | None = None
