"""Unit tests for the full agent graph with FakeLLM (Section 12).

Tests each route (sql, docs, both, refuse), SQL repair recovery, budget guards,
and docs_query generation from SQL results.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from reviewlens.agent.graph import run_question
from reviewlens.agent.nodes import AgentContext
from reviewlens.config import Settings
from reviewlens.llm.fake import FakeLLM


@pytest.fixture
def base_meta():
    return {
        "games": [
            {
                "game": "Brawl Stars",
                "n": 30,
                "date_min": "2024-01-01",
                "date_max": "2024-08-31",
                "versions": [{"version": "57.2.0", "n": 10, "first_seen": "2024-07-01"}],
            }
        ],
        "data_end_date": "2024-08-31",
    }


@pytest.fixture
def mock_qdrant():
    client = MagicMock()
    client.collection_exists.return_value = True
    return client


@pytest.mark.asyncio
async def test_agent_refuse_route_makes_zero_tool_calls(base_meta):
    """Refusal for out_of_scope or unsafe requests terminates immediately."""
    plan_json = json.dumps(
        {
            "intent": "out_of_scope",
            "standalone_question": "Who won the cricket match?",
            "tools": [],
            "reason": "Unrelated topic",
            "filters": {},
        }
    )
    llm = FakeLLM(responses=[plan_json])
    warehouse = MagicMock()
    qdrant = MagicMock()

    ctx = AgentContext(
        llm=llm,
        warehouse=warehouse,
        qdrant_client=qdrant,
        meta=base_meta,
        settings=Settings(),
    )

    result = await run_question(ctx, "Who won the cricket match?")
    ans = result.get("final_answer")
    assert ans is not None
    assert "player reviews of Brawl Stars" in ans.summary
    assert result.get("sql_result") is None
    assert result.get("docs_result") is None
    assert warehouse.execute.call_count == 0


@pytest.mark.asyncio
async def test_agent_sql_only_route(base_meta, tmp_path):
    """SQL-only question executes SQL and synthesizes from SQL#1."""
    import duckdb

    from reviewlens.warehouse.duckdb_backend import DuckDBBackend

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE reviews (game VARCHAR, rating INTEGER, review_date DATE, content VARCHAR)"
    )
    con.execute("INSERT INTO reviews VALUES ('Brawl Stars', 5, '2024-01-01', 'Great')")
    con.close()

    warehouse = DuckDBBackend(db_path)

    plan = {
        "intent": "analytics",
        "standalone_question": "How many reviews for Brawl Stars?",
        "tools": ["sql"],
        "sql_subquestion": "How many reviews for Brawl Stars?",
        "reason": "Counting reviews",
        "filters": {"game": "Brawl Stars"},
    }
    sql_gen = {"sql": "SELECT COUNT(*) AS n FROM reviews", "assumptions": []}
    synth = {
        "summary": "Brawl Stars has 1 review recorded.",
        "findings": [
            {
                "statement": "There is 1 review in the database.",
                "evidence_ids": ["SQL#1"],
                "kind": "observed",
            }
        ],
        "caveats": [],
        "followups": [],
    }

    llm = FakeLLM(responses=[json.dumps(plan), json.dumps(sql_gen), json.dumps(synth)])
    ctx = AgentContext(
        llm=llm,
        warehouse=warehouse,
        qdrant_client=None,
        meta=base_meta,
        settings=Settings(duckdb_path=db_path),
    )

    result = await run_question(ctx, "How many reviews for Brawl Stars?")
    ans = result["final_answer"]
    assert ans is not None
    assert "1 review" in ans.summary
    assert len(ans.findings) == 1
    assert ans.findings[0].evidence_ids == ["SQL#1"]
    assert result.get("sql_result") is not None
    assert result["sql_result"].success is True
    assert result.get("docs_result") is None


@pytest.mark.asyncio
async def test_agent_docs_only_route(base_meta, monkeypatch):
    """Docs-only question executes search and synthesizes with citations."""
    from datetime import date

    from reviewlens.models import RetrievedReview

    mock_doc = RetrievedReview(
        review_id="r101",
        game="Brawl Stars",
        review_date=date(2024, 1, 1),
        rating=1,
        text="It crashes all the time",
        score=0.95,
        rank=1,
    )

    monkeypatch.setattr(
        "reviewlens.search.hybrid.search",
        lambda **kwargs: [mock_doc],
    )

    plan = {
        "intent": "analytics",
        "standalone_question": "What do players say about crashes?",
        "tools": ["docs"],
        "docs_query": "Brawl Stars crash freeze",
        "reason": "Player opinions",
        "filters": {"game": "Brawl Stars"},
    }
    synth = {
        "summary": "Players report frequent game crashes.",
        "findings": [
            {
                "statement": "A player reported constant crashes.",
                "evidence_ids": ["REV:r101"],
                "kind": "player_feedback",
            }
        ],
        "caveats": [],
        "followups": [],
    }

    llm = FakeLLM(responses=[json.dumps(plan), json.dumps(synth)])
    ctx = AgentContext(
        llm=llm,
        warehouse=None,
        qdrant_client=MagicMock(),
        meta=base_meta,
        settings=Settings(),
    )

    result = await run_question(ctx, "What do players say about crashes?")
    ans = result["final_answer"]
    assert ans is not None
    assert len(ans.findings) == 1
    assert ans.findings[0].evidence_ids == ["REV:r101"]
    assert result.get("sql_result") is None
    assert len(result["docs_result"]) == 1


@pytest.mark.asyncio
async def test_agent_budget_guard_returns_partial_answer(base_meta):
    """When AGENT_MAX_LLM_CALLS is exceeded, the agent returns a deterministic partial answer."""
    plan = {
        "intent": "analytics",
        "standalone_question": "Summary",
        "tools": ["docs"],
        "docs_query": "crash",
        "reason": "query",
        "filters": {},
    }
    # Budget = 1: plan consumes it; synthesis cannot make another call
    llm = FakeLLM(responses=[json.dumps(plan)])
    ctx = AgentContext(
        llm=llm,
        warehouse=None,
        qdrant_client=MagicMock(),
        meta=base_meta,
        settings=Settings(agent_max_llm_calls=1),
    )

    result = await run_question(ctx, "What do players say?")
    ans = result["final_answer"]
    assert ans is not None
    assert any("budget" in c.lower() for c in ans.caveats)


@pytest.mark.asyncio
async def test_agent_sequential_hybrid_builds_docs_query_from_sql(base_meta, tmp_path, monkeypatch):
    """Sequential hybrid route: SQL finds lowest version -> docs_query builder creates query -> docs retrieved -> synthesis."""
    from datetime import date

    import duckdb

    from reviewlens.models import RetrievedReview
    from reviewlens.warehouse.duckdb_backend import DuckDBBackend

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE reviews (game VARCHAR, app_version VARCHAR, rating INTEGER, review_date DATE, content VARCHAR)"
    )
    con.execute(
        "INSERT INTO reviews VALUES ('Brawl Stars', '57.2.0', 1, '2024-08-01', 'Buggy update')"
    )
    con.close()

    mock_doc = RetrievedReview(
        review_id="rev_seq",
        game="Brawl Stars",
        review_date=date(2024, 8, 1),
        rating=1,
        app_version="57.2.0",
        text="The 57.2.0 update is very laggy",
        score=0.9,
        rank=1,
    )
    monkeypatch.setattr("reviewlens.search.hybrid.search", lambda **kwargs: [mock_doc])

    warehouse = DuckDBBackend(db_path)

    plan = {
        "intent": "analytics",
        "standalone_question": "Which version is lowest rated and what do reviews say?",
        "tools": ["sql", "docs"],
        "sql_subquestion": "Which version has the lowest rating?",
        "docs_depends_on_sql": True,
        "reason": "Needs version from SQL then search",
        "filters": {"game": "Brawl Stars"},
    }
    sql_gen = {
        "sql": "SELECT app_version, AVG(rating) AS r FROM reviews GROUP BY 1 ORDER BY r ASC LIMIT 1",
        "assumptions": [],
    }
    docs_builder = {
        "query": "version 57.2.0 issues and lag",
        "game": "Brawl Stars",
        "app_versions": ["57.2.0"],
        "rating_min": 1,
        "rating_max": 2,
    }
    synth = {
        "summary": "Version 57.2.0 had the lowest rating and players complain of lag.",
        "findings": [
            {
                "statement": "Version 57.2.0 has an average rating of 1.0.",
                "evidence_ids": ["SQL#1"],
                "kind": "observed",
            },
            {
                "statement": "Players complain about lag in version 57.2.0.",
                "evidence_ids": ["REV:rev_seq"],
                "kind": "player_feedback",
            },
        ],
        "caveats": [],
        "followups": [],
    }

    llm = FakeLLM(
        responses=[
            json.dumps(plan),
            json.dumps(sql_gen),
            json.dumps(docs_builder),
            json.dumps(synth),
        ]
    )
    ctx = AgentContext(
        llm=llm,
        warehouse=warehouse,
        qdrant_client=MagicMock(),
        meta=base_meta,
        settings=Settings(duckdb_path=db_path),
    )

    result = await run_question(ctx, "Which version is lowest rated and what do reviews say?")
    ans = result["final_answer"]
    assert ans is not None
    assert len(ans.findings) == 2
    assert ans.findings[0].evidence_ids == ["SQL#1"]
    assert ans.findings[1].evidence_ids == ["REV:rev_seq"]
