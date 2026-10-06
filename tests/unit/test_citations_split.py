"""Unit tests for cited-vs-retrieved evidence splitting and the verify caveat."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from reviewlens.agent.nodes import (
    SMALL_SAMPLE_SQL_CAVEAT,
    cited_games_caveat,
    cited_ratings_caveat,
    cited_reviews_caveat,
    verify_node,
)
from reviewlens.agent.state import AgentState
from reviewlens.api.citations import split_evidence
from reviewlens.models import (
    FinalAnswer,
    Finding,
    FindingKind,
    Intent,
    Plan,
    RetrievedReview,
    Tool,
)


def _doc(rid: str, game: str = "Marvel Snap", rating: int = 2, score: float = 0.5):
    return RetrievedReview(
        review_id=rid,
        game=game,
        review_date=date(2025, 3, 4),
        rating=rating,
        text=f"text of {rid}",
        score=score,
        rank=1,
    )


def _finding(*ids: str) -> Finding:
    return Finding(statement="s", evidence_ids=list(ids), kind=FindingKind.PLAYER_FEEDBACK)


def test_split_evidence_lists_only_cited_reviews_with_metadata() -> None:
    docs = [_doc("a"), _doc("b", game="Cookie Run: Kingdom", rating=5), _doc("c")]
    ans = FinalAnswer(summary="x", findings=[_finding("REV:b"), _finding("REV:a", "REV:b")])

    citations, remaining = split_evidence(ans, None, docs)

    assert [c.id for c in citations] == ["REV:b", "REV:a"]  # first-cited order, no dupes
    b = citations[0]
    assert (b.type, b.game, b.date, b.rating) == ("review", "Cookie Run: Kingdom", "2025-03-04", 5)
    assert [r.review_id for r in remaining] == ["c"]
    assert remaining[0].rank_score == 0.5
    assert "score" not in remaining[0].model_dump()


def test_split_evidence_nothing_cited_means_everything_remaining() -> None:
    citations, remaining = split_evidence(FinalAnswer(summary="x"), None, [_doc("a"), _doc("b")])
    assert citations == []
    assert [r.review_id for r in remaining] == ["a", "b"]


def test_split_evidence_sql_cited_only_when_a_finding_cites_it() -> None:
    sql = SimpleNamespace(result=SimpleNamespace(sql="SELECT 1"))
    cited, _ = split_evidence(FinalAnswer(summary="x", findings=[_finding("SQL#1")]), sql, [])
    uncited, _ = split_evidence(FinalAnswer(summary="x"), sql, [])
    assert [c.id for c in cited] == ["SQL#1"]
    assert uncited == []


def _verify(
    ans: FinalAnswer,
    evidence: str,
    tools: list[Tool],
    docs: list[RetrievedReview] | None = None,
    sql_result: Any = None,
) -> FinalAnswer:
    plan = Plan(intent=Intent.ANALYTICS, standalone_question="q", tools=tools, reason="r")
    state: AgentState = {"final_answer": ans, "evidence": evidence, "plan": plan}
    if docs is not None:
        state["docs_result"] = docs
    if sql_result is not None:
        state["sql_result"] = sql_result
    return verify_node(state, MagicMock())["final_answer"]


def test_verify_adds_distinct_review_count_caveat() -> None:
    ans = FinalAnswer(
        summary="x",
        findings=[_finding("REV:a"), _finding("REV:a", "REV:b"), _finding("REV:b")],
    )
    out = _verify(ans, '<review id="REV:a">.</review> <review id="REV:b">.</review>', [Tool.DOCS])
    assert cited_reviews_caveat(2) in out.caveats
    assert "Findings are based on 2 distinct reviews." in out.caveats


def test_verify_caveat_counts_after_dropping_invalid_citations() -> None:
    ans = FinalAnswer(summary="x", findings=[_finding("REV:a"), _finding("REV:ghost")])
    out = _verify(ans, '<review id="REV:a">.</review>', [Tool.DOCS])
    assert "Findings are based on 1 distinct review." in out.caveats


def test_verify_caveat_is_not_duplicated_and_states_zero() -> None:
    ans = FinalAnswer(
        summary="x", findings=[], caveats=["Findings are based on 9 distinct reviews."]
    )
    out = _verify(ans, "<evidence></evidence>", [Tool.DOCS])
    assert out.caveats == ["Findings are based on 0 distinct reviews."]


def test_verify_skips_caveat_for_sql_only_answers() -> None:
    ans = FinalAnswer(summary="x", findings=[_finding("SQL#1")])
    out = _verify(ans, '<sql id="SQL#1">[]</sql>', [Tool.SQL])
    assert not any(c.startswith("Findings are based on") for c in out.caveats)


def test_verify_adds_games_and_rating_distribution_caveats() -> None:
    ans = FinalAnswer(
        summary="Summary",
        findings=[
            _finding("REV:r1"),
            _finding("REV:r2"),
            _finding("REV:r3"),
        ],
    )
    docs = [
        _doc("r1", game="Brawl Stars", rating=1),
        _doc("r2", game="Brawl Stars", rating=2),
        _doc("r3", game="Clash Royale", rating=5),
    ]
    evidence = (
        '<review id="REV:r1">.</review> '
        '<review id="REV:r2">.</review> '
        '<review id="REV:r3">.</review>'
    )
    out = _verify(ans, evidence, [Tool.DOCS], docs=docs)
    expected_games = cited_games_caveat({"Brawl Stars": 2, "Clash Royale": 1})
    expected_ratings = cited_ratings_caveat({1: 1, 2: 1, 5: 1})
    assert "Findings are based on 3 distinct reviews." in out.caveats
    assert expected_games in out.caveats
    assert expected_ratings in out.caveats


def test_verify_caveats_are_idempotent() -> None:
    ans = FinalAnswer(
        summary="Summary",
        findings=[_finding("REV:r1")],
        caveats=[
            "Findings are based on 99 distinct reviews.",
            "Cited reviews cover: Old Game (99).",
            "Rating distribution of cited reviews: 1★: 99, 2★: 0, 3★: 0, 4★: 0, 5★: 0.",
        ],
    )
    docs = [_doc("r1", game="Marvel Snap", rating=4)]
    evidence = '<review id="REV:r1">.</review>'
    out = _verify(ans, evidence, [Tool.DOCS], docs=docs)
    assert out.caveats == [
        "Findings are based on 1 distinct review.",
        "Cited reviews cover: Marvel Snap (1).",
        "Rating distribution of cited reviews: 1★: 0, 2★: 0, 3★: 0, 4★: 1, 5★: 0.",
    ]


def test_verify_adds_small_sample_sql_caveat_when_count_below_30() -> None:
    ans = FinalAnswer(summary="Summary", findings=[_finding("SQL#1")])
    evidence = '<sql id="SQL#1">...</sql>'
    sql_tool_res = SimpleNamespace(
        result=SimpleNamespace(
            columns=["app_version", "avg_rating", "review_count"],
            rows=[["1.0.0", 4.5, 50], ["2.0.0", 1.0, 5]],  # 5 < 30
            row_count=2,
        )
    )
    out = _verify(ans, evidence, [Tool.SQL], sql_result=sql_tool_res)
    assert SMALL_SAMPLE_SQL_CAVEAT in out.caveats


def test_verify_no_small_sample_sql_caveat_when_all_counts_30_or_above() -> None:
    ans = FinalAnswer(summary="Summary", findings=[_finding("SQL#1")])
    evidence = '<sql id="SQL#1">...</sql>'
    sql_tool_res = SimpleNamespace(
        result=SimpleNamespace(
            columns=["app_version", "avg_rating", "cnt"],
            rows=[["1.0.0", 4.5, 30], ["2.0.0", 3.2, 120]],
            row_count=2,
        )
    )
    out = _verify(ans, evidence, [Tool.SQL], sql_result=sql_tool_res)
    assert SMALL_SAMPLE_SQL_CAVEAT not in out.caveats


def test_verify_no_small_sample_sql_caveat_without_count_column() -> None:
    ans = FinalAnswer(summary="Summary", findings=[_finding("SQL#1")])
    evidence = '<sql id="SQL#1">...</sql>'
    sql_tool_res = SimpleNamespace(
        result=SimpleNamespace(
            columns=["game", "avg_rating"],
            rows=[["Marvel Snap", 4.2], ["Clash Royale", 3.9]],
            row_count=2,
        )
    )
    out = _verify(ans, evidence, [Tool.SQL], sql_result=sql_tool_res)
    assert SMALL_SAMPLE_SQL_CAVEAT not in out.caveats


def test_verify_small_sample_sql_caveat_is_idempotent() -> None:
    ans = FinalAnswer(
        summary="Summary",
        findings=[_finding("SQL#1")],
        caveats=[SMALL_SAMPLE_SQL_CAVEAT],
    )
    evidence = '<sql id="SQL#1">...</sql>'
    sql_tool_res = SimpleNamespace(
        result=SimpleNamespace(
            columns=["version", "count"],
            rows=[["0.1.0", 1]],
            row_count=1,
        )
    )
    out = _verify(ans, evidence, [Tool.SQL], sql_result=sql_tool_res)
    assert out.caveats.count(SMALL_SAMPLE_SQL_CAVEAT) == 1
