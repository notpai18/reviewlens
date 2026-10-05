"""Unit tests for agent nodes and graph routing."""

from unittest.mock import MagicMock

import pytest

from reviewlens.agent.graph import _route_plan, _route_sql, build_graph
from reviewlens.agent.nodes import AgentContext, plan_node, refuse_node, verify_node
from reviewlens.agent.state import AgentState
from reviewlens.config import Settings
from reviewlens.llm.fake import FakeLLM
from reviewlens.models import FinalAnswer, Finding, FindingKind, Intent, Plan, Tool


def test_route_plan_refuse():
    state: AgentState = {
        "plan": Plan(intent=Intent.OUT_OF_SCOPE, standalone_question="x", tools=[], reason="y")
    }
    assert _route_plan(state) == "refuse"


def test_route_plan_sql():
    state: AgentState = {
        "plan": Plan(intent=Intent.ANALYTICS, standalone_question="x", tools=[Tool.SQL], reason="y")
    }
    assert _route_plan(state) == "sql_tool"


def test_route_plan_docs():
    state: AgentState = {
        "plan": Plan(
            intent=Intent.ANALYTICS, standalone_question="x", tools=[Tool.DOCS], reason="y"
        )
    }
    assert _route_plan(state) == "docs_tool"


def test_route_plan_synthesize():
    state: AgentState = {
        "plan": Plan(intent=Intent.ANALYTICS, standalone_question="x", tools=[], reason="y")
    }
    assert _route_plan(state) == "synthesize"


def test_route_sql_to_docs():
    state: AgentState = {
        "plan": Plan(
            intent=Intent.ANALYTICS,
            standalone_question="x",
            tools=[Tool.SQL, Tool.DOCS],
            reason="y",
        )
    }
    assert _route_sql(state) == "docs_tool"


def test_route_sql_to_synthesize():
    state: AgentState = {
        "plan": Plan(intent=Intent.ANALYTICS, standalone_question="x", tools=[Tool.SQL], reason="y")
    }
    assert _route_sql(state) == "synthesize"


@pytest.mark.asyncio
async def test_plan_node_validation():
    # Test that plan_node clamps ratings and drops unknown games
    llm = FakeLLM(
        responses=[
            '{"intent": "analytics", "standalone_question": "q", "tools": ["sql"], "filters": {"game": "UnknownGame", "rating_min": 0, "rating_max": 6}, "reason": "test"}'
        ]
    )
    ctx = AgentContext(
        llm=llm,
        warehouse=None,
        qdrant_client=None,
        meta={"games": [{"game": "KnownGame"}]},
        settings=Settings(),
    )
    state: AgentState = {"question": "q"}

    res = await plan_node(state, ctx)
    plan = res["plan"]

    # game should be dropped because it's unknown
    assert plan.filters.game is None
    # ratings should be clamped to 1..5
    assert plan.filters.rating_min == 1
    assert plan.filters.rating_max == 5


def test_verify_node_keeps_valid_findings():
    ans = FinalAnswer(
        summary="sum",
        findings=[
            Finding(statement="f1", evidence_ids=["SQL#1"], kind=FindingKind.OBSERVED),
            Finding(statement="f2", evidence_ids=["REV:123"], kind=FindingKind.PLAYER_FEEDBACK),
        ],
    )
    state: AgentState = {
        "final_answer": ans,
        "evidence": '<sql id="SQL#1">...</sql> <review id="REV:123">...</review>',
    }

    res = verify_node(state, MagicMock())
    final_ans = res["final_answer"]
    assert len(final_ans.findings) == 2


def test_verify_node_drops_invalid_findings_and_adds_caveat():
    ans = FinalAnswer(
        summary="sum",
        findings=[
            Finding(statement="f1", evidence_ids=["SQL#1"], kind=FindingKind.OBSERVED),
            Finding(
                statement="f2", evidence_ids=["REV:999"], kind=FindingKind.PLAYER_FEEDBACK
            ),  # missing
        ],
    )
    state: AgentState = {
        "final_answer": ans,
        "evidence": '<sql id="SQL#1">...</sql>',
    }

    res = verify_node(state, MagicMock())
    final_ans = res["final_answer"]
    assert len(final_ans.findings) == 1
    assert final_ans.findings[0].evidence_ids == ["SQL#1"]


def test_verify_node_all_dropped():
    ans = FinalAnswer(
        summary="sum",
        findings=[
            Finding(statement="f1", evidence_ids=["SQL#999"], kind=FindingKind.OBSERVED),
        ],
    )
    state: AgentState = {
        "final_answer": ans,
        "evidence": '<sql id="SQL#1">...</sql>',
    }

    res = verify_node(state, MagicMock())
    final_ans = res["final_answer"]
    assert len(final_ans.findings) == 0
    assert "Evidence could not be verified." in final_ans.caveats


def test_refuse_node_unsafe():
    plan = Plan(intent=Intent.UNSAFE_REQUEST, standalone_question="q", tools=[], reason="r")
    state: AgentState = {"plan": plan, "question": "q"}
    ctx = AgentContext(
        llm=None,
        warehouse=None,
        qdrant_client=None,
        meta={"games": [{"game": "A"}]},
        settings=Settings(),
    )

    res = refuse_node(state, ctx)
    assert "refusal_reason" in res
    assert "modify data or reveal" in res["refusal_reason"]
    assert res["final_answer"].summary == res["refusal_reason"]


def test_refuse_node_out_of_scope():
    plan = Plan(intent=Intent.OUT_OF_SCOPE, standalone_question="q", tools=[], reason="r")
    state: AgentState = {"plan": plan, "question": "q"}
    ctx = AgentContext(
        llm=None,
        warehouse=None,
        qdrant_client=None,
        meta={"games": [{"game": "Game1"}, {"game": "Game2"}]},
        settings=Settings(),
    )

    res = refuse_node(state, ctx)
    assert "refusal_reason" in res
    assert "Game1 and Game2" in res["refusal_reason"]
    assert res["final_answer"].summary == res["refusal_reason"]


def test_build_graph():
    ctx = AgentContext(
        llm=None, warehouse=None, qdrant_client=None, meta={"games": []}, settings=Settings()
    )
    graph = build_graph(ctx)
    assert graph is not None
    # We could also invoke it on a simple state, but just compiling is good.
