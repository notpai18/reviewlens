"""Unit tests for the docs-query stopword filter and its use by the docs tool."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from reviewlens.agent.graph import run_question
from reviewlens.agent.nodes import AgentContext
from reviewlens.agent.query import STOPWORDS, clean_docs_query
from reviewlens.config import Settings
from reviewlens.llm.fake import FakeLLM
from reviewlens.models import RetrievedReview


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("combat gameplay feedback opinions complaints praise", "combat gameplay"),
        (
            "ads advertisements commercial player complaints and feedback",
            "ads advertisements commercial",
        ),
        ("What are players saying about the combat?", "combat"),
        ("reviews players say about crashes", "crashes"),
        ("  Feedback,  OPINIONS!  Combat   ", "Combat"),
        ("pay-to-win gacha rates", "pay-to-win gacha rates"),
    ],
)
def test_clean_docs_query_removes_generic_words(raw: str, expected: str) -> None:
    assert clean_docs_query(raw) == expected


def test_clean_docs_query_preserves_topical_words_and_order() -> None:
    assert clean_docs_query("matchmaking lag crash freeze") == "matchmaking lag crash freeze"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pay to win", "pay to win"),
        ("log in", "log in"),
        ("customer service", "customer service"),
        ("lost progress", "lost progress"),
        ("What are players saying about pay to win mechanics?", "pay to win mechanics"),
        ("player complaints about log in issues", "log in issues"),
        ("bad customer service reviews and feedback", "bad customer service"),
        ("users opinions on lost progress bug", "lost progress bug"),
    ],
)
def test_clean_docs_query_preserves_multi_word_phrases(raw: str, expected: str) -> None:
    assert clean_docs_query(raw) == expected


def test_clean_docs_query_uses_fallback_when_nothing_topical_left() -> None:
    assert clean_docs_query("players feedback", fallback="What do players say about ads?") == "ads"


def test_clean_docs_query_never_returns_empty_for_nonempty_input() -> None:
    assert clean_docs_query("players feedback") == "players feedback"
    assert clean_docs_query("players feedback", fallback="what do players say") == (
        "players feedback"
    )


@pytest.mark.parametrize(
    "word",
    ["feedback", "opinions", "complaints", "praise", "reviews", "players", "say"],
)
def test_required_generic_words_are_stopwords(word: str) -> None:
    assert word in STOPWORDS


@pytest.mark.asyncio
async def test_docs_tool_searches_with_cleaned_query(monkeypatch) -> None:
    """Even if the planner pads docs_query, the search runs on topical words only."""
    seen: dict[str, str] = {}
    doc = RetrievedReview(
        review_id="r1",
        game="Brawl Stars",
        review_date=date(2024, 1, 1),
        rating=2,
        text="The combat is too automated",
        score=0.5,
        rank=1,
    )

    def fake_search(**kwargs):
        seen["query"] = kwargs["query"]
        return [doc]

    monkeypatch.setattr("reviewlens.search.hybrid.search", fake_search)

    plan = {
        "intent": "analytics",
        "standalone_question": "What are players saying about the combat?",
        "tools": ["docs"],
        "docs_query": "combat gameplay feedback opinions complaints praise",
        "reason": "Player opinions",
        "filters": {},
    }
    synth = {
        "summary": "One review mentions combat.",
        "findings": [
            {
                "statement": "A Brawl Stars reviewer says combat is too automated.",
                "evidence_ids": ["REV:r1"],
                "kind": "player_feedback",
            }
        ],
        "caveats": [],
        "followups": [],
    }
    meta = {"games": [{"game": "Brawl Stars", "versions": []}]}
    ctx = AgentContext(
        llm=FakeLLM(responses=[json.dumps(plan), json.dumps(synth)]),
        warehouse=None,
        qdrant_client=MagicMock(),
        meta=meta,
        settings=Settings(),
    )

    result = await run_question(ctx, "What are players saying about the combat?")

    assert seen["query"] == "combat gameplay"
    assert any("Removed generic words" in t.summary for t in result["trace"])
