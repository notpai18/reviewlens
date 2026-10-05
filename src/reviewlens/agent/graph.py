"""Agent graph definition."""

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from reviewlens.agent.nodes import (
    AgentContext,
    docs_tool_node,
    plan_node,
    refuse_node,
    sql_tool_node,
    synthesize_node,
    verify_node,
)
from reviewlens.agent.state import AgentState
from reviewlens.models import Intent, Tool


def _route_plan(state: AgentState) -> str:
    """Route after planning."""
    plan = state["plan"]
    if not plan:
        return "refuse"

    if plan.intent != Intent.ANALYTICS:
        return "refuse"

    if Tool.SQL in plan.tools:
        return "sql_tool"
    elif Tool.DOCS in plan.tools:
        return "docs_tool"
    else:
        return "synthesize"


def _route_sql(state: AgentState) -> str:
    """Route after SQL tool."""
    plan = state["plan"]
    if plan and Tool.DOCS in plan.tools:
        return "docs_tool"
    return "synthesize"


def build_graph(ctx: AgentContext) -> Any:
    """Build and return the LangGraph agent."""
    builder: Any = StateGraph(AgentState)

    # Add nodes with injected context
    builder.add_node("plan", partial(plan_node, ctx=ctx))
    builder.add_node("sql_tool", partial(sql_tool_node, ctx=ctx))
    builder.add_node("docs_tool", partial(docs_tool_node, ctx=ctx))
    builder.add_node("synthesize", partial(synthesize_node, ctx=ctx))
    builder.add_node("verify", partial(verify_node, ctx=ctx))
    builder.add_node("refuse", partial(refuse_node, ctx=ctx))

    # Add edges
    builder.add_edge(START, "plan")

    builder.add_conditional_edges(
        "plan",
        _route_plan,
        {
            "refuse": "refuse",
            "sql_tool": "sql_tool",
            "docs_tool": "docs_tool",
            "synthesize": "synthesize",
        },
    )

    builder.add_conditional_edges(
        "sql_tool",
        _route_sql,
        {
            "docs_tool": "docs_tool",
            "synthesize": "synthesize",
        },
    )

    builder.add_edge("docs_tool", "synthesize")
    builder.add_edge("synthesize", "verify")
    builder.add_edge("verify", END)
    builder.add_edge("refuse", END)

    return builder.compile()


async def run_question(
    ctx: AgentContext, question: str, history: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """Run the graph for one question, honouring AGENT_TIMEOUT_S."""
    import asyncio

    graph = build_graph(ctx)
    state_in: dict[str, Any] = {"question": question, "history": history or []}
    result: dict[str, Any] = await asyncio.wait_for(
        graph.ainvoke(state_in), timeout=ctx.settings.agent_timeout_s
    )
    return result


async def _cli(question: str) -> int:
    from reviewlens.agent.evidence import render_answer_markdown
    from reviewlens.agent.factory import build_components, make_context
    from reviewlens.config import get_settings

    settings = get_settings()
    if not settings.gemini_api_key:
        import json

        from reviewlens.llm.fake import FakeLLM

        print("NOTE: GEMINI_API_KEY is not set (human task H2). Running with FakeLLM demo agent.")
        plan = {
            "intent": "analytics",
            "standalone_question": question,
            "tools": ["sql", "docs"],
            "docs_query": "matchmaking crash",
            "reason": "Analyze ratings and review feedback",
            "filters": {"game": "Brawl Stars"},
        }
        sql_gen = {
            "sql": "SELECT game, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS n FROM reviews GROUP BY game",
            "assumptions": ["Aggregated by game across all recorded dates"],
        }
        synth = {
            "summary": "Brawl Stars has an average rating of 4.1. Players frequently report matchmaking latency and bugs.",
            "findings": [
                {
                    "statement": "Brawl Stars averages 4.1 stars across reviews.",
                    "evidence_ids": ["SQL#1"],
                    "kind": "observed",
                },
                {
                    "statement": "Players report matchmaking freezes.",
                    "evidence_ids": [],
                    "kind": "player_feedback",
                },
            ],
            "caveats": ["Demonstration answer generated using FakeLLM simulator."],
            "followups": ["Examine matchmaking latency by app version."],
        }
        components = build_components(settings)
        components.llm = FakeLLM(
            responses=[json.dumps(plan), json.dumps(sql_gen), json.dumps(synth)]
        )
        ctx = make_context(components, settings)
    else:
        ctx = make_context(build_components(settings), settings)
    print(f"\nQuestion: {question}\n")
    try:
        result = await run_question(ctx, question)
    except TimeoutError:
        print("=== ERROR ===")
        print(f"Agent execution timed out after {ctx.settings.agent_timeout_s} seconds.")
        return 1

    ans = result.get("final_answer")
    print("=== ANSWER ===")
    print(render_answer_markdown(ans) if ans else "(no answer)")

    sql_res = result.get("sql_result")
    if sql_res:
        print("\n=== SQL ===")
        for a in sql_res.attempts:
            print(f"  attempt {a.attempt_num}: {a.status}" + (f" ({a.error})" if a.error else ""))
        if sql_res.final_sql:
            print(f"  final: {sql_res.final_sql}")

    docs = result.get("docs_result") or []
    if docs:
        print("\n=== RETRIEVED ===")
        for d in docs:
            print(f"  REV:{d.review_id} [{d.rating}*] {d.text[:90]!r}")

    print("\n=== TRACE ===")
    for ev in result.get("trace", []):
        print(f"  {ev.node:<11} {ev.duration_ms:>6} ms  {ev.summary}")
    usage = result.get("usage")
    if usage:
        print(
            f"\nUsage: {usage.llm_calls} LLM calls, "
            f"{usage.prompt_tokens}+{usage.completion_tokens} tokens"
        )
    return 0


if __name__ == "__main__":
    import asyncio
    import sys

    if len(sys.argv) < 2:
        print('Usage: python -m reviewlens.agent.graph "<question>"')
        sys.exit(1)
    sys.exit(asyncio.run(_cli(" ".join(sys.argv[1:]))))
