"""Unit tests for models and data schemas."""

from datetime import date

from reviewlens.models import (
    APIRequest,
    APIResponse,
    Citation,
    Filters,
    FinalAnswer,
    Finding,
    FindingKind,
    Intent,
    Plan,
    RetrievalMode,
    RetrievedReview,
    SQLResult,
    Tool,
    TraceEvent,
    Usage,
    ValidationResult,
)


def test_intent_and_tool_enums():
    """Test intent and tool enums."""
    assert Intent.ANALYTICS == "analytics"
    assert Intent.OUT_OF_SCOPE == "out_of_scope"
    assert Intent.UNSAFE_REQUEST == "unsafe_request"

    assert Tool.SQL == "sql"
    assert Tool.DOCS == "docs"

    assert FindingKind.OBSERVED == "observed"
    assert FindingKind.PLAYER_FEEDBACK == "player_feedback"

    assert RetrievalMode.HYBRID_RRF == "hybrid_rrf"
    assert RetrievalMode.BM25 == "bm25"
    assert RetrievalMode.DENSE == "dense"


def test_filters_model():
    """Test Filters model."""
    filters = Filters(
        game="Clash of Clans",
        rating_min=1,
        rating_max=3,
        date_from=date(2024, 1, 1),
        date_to=date(2024, 1, 31),
        app_versions=["1.0.0", "1.1.0"],
    )
    assert filters.game == "Clash of Clans"
    assert filters.rating_min == 1
    assert filters.rating_max == 3
    assert len(filters.app_versions or []) == 2


def test_plan_model():
    """Test Plan model."""
    plan = Plan(
        intent=Intent.ANALYTICS,
        standalone_question="How many reviews are there?",
        tools=[Tool.SQL],
        sql_subquestion="Count reviews per game",
        reason="Counting requires SQL aggregation",
    )
    assert plan.intent == Intent.ANALYTICS
    assert Tool.SQL in plan.tools
    assert plan.docs_depends_on_sql is False


def test_sql_result_and_validation():
    """Test SQL result and validation models."""
    res = SQLResult(
        sql="SELECT 1",
        columns=["a"],
        rows=[[1]],
        row_count=1,
        truncated=False,
        elapsed_ms=10,
    )
    assert res.row_count == 1
    assert res.error is None

    val = ValidationResult(ok=True, normalized_sql="SELECT 1 LIMIT 500")
    assert val.ok is True
    assert len(val.reasons) == 0


def test_retrieved_review():
    """Test RetrievedReview model."""
    rev = RetrievedReview(
        review_id="rev123",
        game="Game A",
        review_date=date(2024, 5, 10),
        rating=5,
        app_version="2.0",
        text="Great game!",
        score=0.95,
        rank=1,
    )
    assert rev.review_id == "rev123"
    assert rev.score == 0.95


def test_final_answer_and_api_response():
    """Test FinalAnswer and APIResponse models."""
    finding = Finding(
        statement="Game has 4.5 average rating",
        evidence_ids=["SQL#1"],
        kind=FindingKind.OBSERVED,
    )
    answer = FinalAnswer(
        summary="Here is the summary.",
        findings=[finding],
        caveats=["Small sample"],
        followups=["What about Game B?"],
    )
    assert len(answer.findings) == 1

    citation = Citation(
        id="SQL#1",
        type="sql",
        label="Average rating query",
    )

    resp = APIResponse(
        request_id="req_123",
        answer_markdown="## Answer\nHere is the summary.",
        answer=answer,
        citations=[citation],
        trace=[TraceEvent(node="plan", duration_ms=50, summary="Planned query")],
        usage=Usage(llm_calls=2, prompt_tokens=100, completion_tokens=50, latency_ms=200),
    )
    assert resp.request_id == "req_123"
    assert resp.usage.llm_calls == 2
    assert resp.cached is False


def test_api_request():
    """Test APIRequest model."""
    req = APIRequest(
        question="What is the rating of Game A?",
        history=[{"role": "user", "content": "Hi"}],
    )
    assert req.question == "What is the rating of Game A?"
    assert len(req.history) == 1
