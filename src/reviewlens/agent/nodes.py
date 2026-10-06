"""Graph nodes for the ReviewLens agent."""

import asyncio
import json
import re
import time
from typing import Any

from pydantic import BaseModel

from reviewlens.agent.evidence import format_evidence, valid_evidence_ids
from reviewlens.agent.query import clean_docs_query
from reviewlens.agent.state import AgentState
from reviewlens.config import Settings
from reviewlens.llm.budget import BudgetedLLM, LLMBudgetExceeded
from reviewlens.models import (
    Filters,
    FinalAnswer,
    Finding,
    FindingKind,
    Intent,
    Plan,
    RetrievedReview,
    Tool,
    TraceEvent,
    Usage,
)
from reviewlens.prompts.renderer import load_and_render
from reviewlens.sql.generate import SQLGenerator
from reviewlens.warehouse.catalog import meta_text

UNVERIFIED_CAVEAT = "Evidence could not be verified."
CITED_REVIEWS_PREFIX = "Findings are based on "
CITED_GAMES_PREFIX = "Cited reviews cover: "
CITED_RATINGS_PREFIX = "Rating distribution of cited reviews: "
SMALL_SAMPLE_SQL_CAVEAT = "Some reported results are based on fewer than 30 reviews."


class DocsQueryResponse(BaseModel):
    """Dynamic query generated from SQL findings."""

    query: str
    game: str | None = None
    app_versions: list[str] | None = None
    rating_min: int | None = None
    rating_max: int | None = None


class AgentContext:
    """Holds dependencies and configurations for the agent graph.

    A new context (and therefore a new LLM call budget) is created per request.
    """

    def __init__(
        self,
        llm: Any,
        warehouse: Any,
        qdrant_client: Any,
        meta: dict[str, Any],
        settings: Settings,
    ):
        if (
            llm is not None
            and settings.agent_max_llm_calls > 0
            and not isinstance(llm, BudgetedLLM)
        ):
            llm = BudgetedLLM(llm, settings.agent_max_llm_calls)
        self.llm = llm
        self.warehouse = warehouse
        self.qdrant_client = qdrant_client
        self.meta = meta
        self.meta_text = meta_text(meta)
        self.settings = settings
        self.sql_gen = SQLGenerator(
            llm=llm,
            warehouse=warehouse,
            meta_path=settings.data_meta_path if settings.data_meta_path.exists() else None,
            max_attempts=settings.sql_max_attempts,
            max_rows=settings.sql_max_rows,
        )


def _format_history(history: list[dict[str, str]] | None) -> str:
    if not history:
        return "No history."
    return "\n".join(f"{msg['role']}: {msg['content']}" for msg in history)


def _known_games(meta: dict[str, Any]) -> set[str]:
    return {g["game"] for g in meta.get("games", [])}


def _known_versions(meta: dict[str, Any]) -> set[str]:
    """Collect version strings. meta.json stores versions as objects ({"version": ...})."""
    versions: set[str] = set()
    for g in meta.get("games", []):
        for v in g.get("versions", []):
            versions.add(str(v["version"]) if isinstance(v, dict) else str(v))
    return versions


def _clamp_rating(value: int | None) -> int | None:
    return None if value is None else max(1, min(5, value))


async def plan_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: generate an execution plan."""
    t0 = time.monotonic()

    prompt = load_and_render(
        "plan.md",
        meta_text=ctx.meta_text,
        history_text=_format_history(state.get("history")),
        question=state["question"],
    )

    result = await ctx.llm.generate_structured(
        system="You are the planning module of ReviewLens. Return JSON only.",
        prompt=prompt,
        schema=Plan,
    )
    plan = Plan.model_validate(result.data)

    # Post-process validation (Section 9.3)
    if plan.filters.game and plan.filters.game not in _known_games(ctx.meta):
        plan.filters.game = None

    if plan.filters.app_versions:
        known_versions = _known_versions(ctx.meta)
        plan.filters.app_versions = [v for v in plan.filters.app_versions if v in known_versions]
        if not plan.filters.app_versions:
            plan.filters.app_versions = None

    plan.filters.rating_min = _clamp_rating(plan.filters.rating_min)
    plan.filters.rating_max = _clamp_rating(plan.filters.rating_max)

    if plan.intent != Intent.ANALYTICS:
        plan.tools = []
    elif not plan.tools:
        plan.tools = [Tool.DOCS]

    duration = int((time.monotonic() - t0) * 1000)
    trace = [
        TraceEvent(
            node="plan",
            duration_ms=duration,
            summary=f"intent={plan.intent}, tools={[str(t) for t in plan.tools]}",
        )
    ]
    usage = Usage(
        llm_calls=1,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        latency_ms=result.latency_ms,
    )

    return {"plan": plan, "trace": trace, "usage": usage}


async def sql_tool_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: generate and execute SQL."""
    t0 = time.monotonic()
    plan = state["plan"]
    assert plan is not None

    question = plan.sql_subquestion or plan.standalone_question
    sql_result = await ctx.sql_gen.run(question)

    duration = int((time.monotonic() - t0) * 1000)
    summary = f"SQL success: {sql_result.success}, attempts: {len(sql_result.attempts)}"
    if sql_result.result:
        summary += f" ({sql_result.result.row_count} rows)"

    out: dict[str, Any] = {
        "sql_result": sql_result,
        "trace": [TraceEvent(node="sql_tool", duration_ms=duration, summary=summary)],
        "usage": sql_result.usage,
    }
    if not sql_result.success and sql_result.error_summary:
        out["errors"] = [f"SQL error: {sql_result.error_summary}"]
    return out


async def docs_tool_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: retrieve reviews."""
    from reviewlens.search.hybrid import search

    t0 = time.monotonic()
    plan = state["plan"]
    assert plan is not None
    usage = Usage()
    trace_events: list[TraceEvent] = []
    errors: list[str] = []

    filters = plan.filters.model_copy(deep=True)
    query = plan.docs_query or plan.standalone_question

    sql_result = state.get("sql_result")
    if plan.docs_depends_on_sql and sql_result and sql_result.success:
        sql_res = sql_result.result
        if sql_res and sql_res.row_count > 0:
            compact_res = {
                "columns": sql_res.columns,
                "rows": sql_res.rows[:5],  # small preview
            }
            prompt = load_and_render(
                "docs_query.md",
                question=plan.standalone_question,
                sql_summary=json.dumps(compact_res, default=str),
            )
            try:
                result = await ctx.llm.generate_structured(
                    system="You build search queries.", prompt=prompt, schema=DocsQueryResponse
                )
            except LLMBudgetExceeded:
                trace_events.append(
                    TraceEvent(
                        node="docs_tool",
                        duration_ms=0,
                        summary="LLM budget exhausted; using the planner's docs query.",
                    )
                )
            else:
                dqr = DocsQueryResponse.model_validate(result.data)
                usage.llm_calls += 1
                usage.prompt_tokens += result.prompt_tokens
                usage.completion_tokens += result.completion_tokens
                usage.latency_ms += result.latency_ms
                query = dqr.query or query
                if dqr.game and dqr.game in _known_games(ctx.meta):
                    filters.game = dqr.game
                if dqr.app_versions:
                    versions = [v for v in dqr.app_versions if v in _known_versions(ctx.meta)]
                    if versions:
                        filters.app_versions = versions
                if dqr.rating_min is not None:
                    filters.rating_min = _clamp_rating(dqr.rating_min)
                if dqr.rating_max is not None:
                    filters.rating_max = _clamp_rating(dqr.rating_max)

    # Backstop: strip generic words (feedback, complaints, players, say, ...) the planner
    # may have left in the query; they match unrelated reviews.
    cleaned_query = clean_docs_query(query, fallback=plan.standalone_question)
    if cleaned_query != query:
        trace_events.append(
            TraceEvent(
                node="docs_tool",
                duration_ms=0,
                summary=f"Removed generic words from docs query: {query!r} -> {cleaned_query!r}.",
            )
        )
        query = cleaned_query

    # Retrieval
    def do_search(flt: Filters) -> list[RetrievedReview]:
        return search(
            client=ctx.qdrant_client,
            collection=ctx.settings.qdrant_collection,
            query=query,
            mode=ctx.settings.retrieval_mode,
            filters=flt,
            k=ctx.settings.retrieval_top_k,
        )

    docs: list[RetrievedReview] = []
    try:
        docs = await asyncio.to_thread(do_search, filters)

        # Retry fallback (keep game and versions; drop date and rating filters)
        if len(docs) < 3 and (
            filters.date_from
            or filters.date_to
            or filters.rating_min is not None
            or filters.rating_max is not None
        ):
            trace_events.append(
                TraceEvent(
                    node="docs_tool",
                    duration_ms=0,
                    summary="Fewer than 3 results; retrying without date/rating filters.",
                )
            )
            filters.date_from = None
            filters.date_to = None
            filters.rating_min = None
            filters.rating_max = None
            docs = await asyncio.to_thread(do_search, filters)
    except Exception as exc:  # search backend down, model missing, ...
        errors.append(f"Review search error: {type(exc).__name__}: {exc}")
        trace_events.append(
            TraceEvent(node="docs_tool", duration_ms=0, summary=f"Search failed: {exc}"[:200])
        )

    duration = int((time.monotonic() - t0) * 1000)
    trace_events.append(
        TraceEvent(
            node="docs_tool",
            duration_ms=duration,
            summary=f"Retrieved {len(docs)} documents (query={query!r}).",
        )
    )

    out: dict[str, Any] = {"docs_result": docs, "trace": trace_events, "usage": usage}
    if errors:
        out["errors"] = errors
    return out


def _partial_answer(
    sql_result: Any, docs: list[RetrievedReview] | None, reason: str
) -> FinalAnswer:
    """Deterministic fallback answer when the LLM can't be called (budget exhausted)."""
    findings: list[Finding] = []
    if sql_result and sql_result.result:
        r = sql_result.result
        findings.append(
            Finding(
                statement=f"The database query returned {r.row_count} row(s) "
                f"with columns: {', '.join(r.columns)}.",
                evidence_ids=["SQL#1"],
                kind=FindingKind.OBSERVED,
            )
        )
    for rev in (docs or [])[: 5 - len(findings)]:
        snippet = rev.text[:160].replace("\n", " ")
        findings.append(
            Finding(
                statement=f'A {rev.rating}-star review says: "{snippet}"',
                evidence_ids=[f"REV:{rev.review_id}"],
                kind=FindingKind.PLAYER_FEEDBACK,
            )
        )
    return FinalAnswer(
        summary="I could not finish writing a full answer, so here is the raw evidence I found.",
        findings=findings,
        caveats=[reason],
        followups=[],
    )


async def synthesize_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: synthesize final answer."""
    t0 = time.monotonic()
    plan = state["plan"]
    sql_result = state.get("sql_result")
    docs = state.get("docs_result")

    if plan is None or not plan.tools:
        evidence = "<evidence>\n</evidence>"
        errors = "No tools used; out of scope or unsafe."
    else:
        evidence = format_evidence(
            sql_result, docs, sql_question=(plan.sql_subquestion or plan.standalone_question)
        )
        errors = "; ".join(state.get("errors") or []) or "None"

    prompt = load_and_render(
        "synthesize.md",
        question=plan.standalone_question if plan else state["question"],
        evidence_block=evidence,
        errors=errors,
    )

    try:
        result = await ctx.llm.generate_structured(
            system="You are the synthesis module. Answer strictly from evidence.",
            prompt=prompt,
            schema=FinalAnswer,
        )
    except LLMBudgetExceeded as exc:
        duration = int((time.monotonic() - t0) * 1000)
        ans = _partial_answer(
            sql_result, docs, f"Partial answer: {exc}. Raise AGENT_MAX_LLM_CALLS to allow more."
        )
        return {
            "final_answer": ans,
            "evidence": evidence,
            "trace": [
                TraceEvent(
                    node="synthesize",
                    duration_ms=duration,
                    summary="LLM budget exhausted; returned deterministic partial answer.",
                )
            ],
        }

    ans = FinalAnswer.model_validate(result.data)

    duration = int((time.monotonic() - t0) * 1000)
    trace = [TraceEvent(node="synthesize", duration_ms=duration, summary="Synthesized answer.")]
    usage = Usage(
        llm_calls=1,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        latency_ms=result.latency_ms,
    )

    return {"final_answer": ans, "evidence": evidence, "trace": trace, "usage": usage}


def distinct_cited_reviews(ans: FinalAnswer) -> list[str]:
    """Distinct review evidence ids cited by the findings, in first-cited order."""
    seen: dict[str, None] = {}
    for f in ans.findings:
        for eid in f.evidence_ids:
            if eid.startswith("REV:"):
                seen.setdefault(eid, None)
    return list(seen)


def cited_reviews_caveat(n: int) -> str:
    noun = "review" if n == 1 else "reviews"
    return f"{CITED_REVIEWS_PREFIX}{n} distinct {noun}."


def cited_games_caveat(game_counts: dict[str, int]) -> str:
    """Deterministic caveat listing games cited and counts, sorted alphabetically."""
    items = [f"{g} ({c})" for g, c in sorted(game_counts.items())]
    return f"{CITED_GAMES_PREFIX}{', '.join(items)}."


def cited_ratings_caveat(rating_counts: dict[int, int]) -> str:
    """Deterministic caveat listing 1★ through 5★ rating distribution of cited reviews."""
    items = [f"{star}★: {rating_counts.get(star, 0)}" for star in range(1, 6)]
    return f"{CITED_RATINGS_PREFIX}{', '.join(items)}."


def has_sql_row_below_threshold(sql_res: Any, threshold: int = 30) -> bool:
    """Return True if any row in SQL result has a count column value below threshold."""
    if not sql_res:
        return False
    columns = getattr(sql_res, "columns", None)
    if columns is None and isinstance(sql_res, dict):
        columns = sql_res.get("columns")
    rows = getattr(sql_res, "rows", None)
    if rows is None and isinstance(sql_res, dict):
        rows = sql_res.get("rows")
    if not columns or not rows:
        return False

    count_indices: list[int] = []
    for idx, col in enumerate(columns):
        col_lower = str(col).lower()
        if "count" in col_lower or col_lower in (
            "cnt",
            "n",
            "num_reviews",
            "review_count",
            "total_reviews",
            "reviews",
        ):
            count_indices.append(idx)

    if not count_indices:
        return False

    for row in rows:
        for idx in count_indices:
            if idx < len(row):
                val = row[idx]
                if isinstance(val, (int, float)) and val < threshold:
                    return True
    return False


def verify_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: verify citations (pure Python, no retry loop)."""
    t0 = time.monotonic()
    ans = state.get("final_answer")
    if not ans:
        return {}

    valid_ids = valid_evidence_ids(state.get("evidence") or "")
    valid_findings = [
        f for f in ans.findings if f.evidence_ids and set(f.evidence_ids) <= valid_ids
    ]
    dropped = len(ans.findings) - len(valid_findings)

    if dropped:
        ans.findings = valid_findings
        if not ans.findings and UNVERIFIED_CAVEAT not in ans.caveats:
            ans.caveats.append(UNVERIFIED_CAVEAT)

    if not ans.summary.strip():
        ans.summary = "Summary generation failed."

    # Deterministic caveats: distinct reviews, games, and rating distribution.
    plan = state.get("plan")
    cited_reviews = distinct_cited_reviews(ans)
    if cited_reviews or (plan is not None and Tool.DOCS in plan.tools):
        ans.caveats = [
            c
            for c in ans.caveats
            if not c.startswith(CITED_REVIEWS_PREFIX)
            and not c.startswith(CITED_GAMES_PREFIX)
            and not c.startswith(CITED_RATINGS_PREFIX)
        ]
        ans.caveats.append(cited_reviews_caveat(len(cited_reviews)))

        if cited_reviews:
            docs = state.get("docs_result") or []
            meta_by_id: dict[str, tuple[str, int]] = {
                d.review_id: (d.game, d.rating) for d in docs if d.review_id
            }
            evidence_text = state.get("evidence") or ""
            if evidence_text:
                for match in re.finditer(
                    r"\[REV:(?P<id>[^\s\]]+)\]\s*(?P<game>[^|]+?)\s*\|\s*[^|]+?\|\s*(?P<rating>[1-5])★",
                    evidence_text,
                ):
                    rid = match.group("id")
                    if rid not in meta_by_id:
                        meta_by_id[rid] = (match.group("game").strip(), int(match.group("rating")))

            game_counts: dict[str, int] = {}
            rating_counts: dict[int, int] = {}
            for eid in cited_reviews:
                rid = eid[4:] if eid.startswith("REV:") else eid
                if rid in meta_by_id:
                    game, rating = meta_by_id[rid]
                    game_counts[game] = game_counts.get(game, 0) + 1
                    rating_counts[rating] = rating_counts.get(rating, 0) + 1

            if game_counts:
                ans.caveats.append(cited_games_caveat(game_counts))
            if rating_counts:
                ans.caveats.append(cited_ratings_caveat(rating_counts))

    # Deterministic caveat when any SQL result row has a count below 30
    ans.caveats = [c for c in ans.caveats if c != SMALL_SAMPLE_SQL_CAVEAT]
    sql_result = state.get("sql_result")
    sql_res = getattr(sql_result, "result", None) if sql_result else None
    if sql_res is None and isinstance(sql_result, dict):
        sql_res = sql_result.get("result")
    if has_sql_row_below_threshold(sql_res, threshold=30):
        ans.caveats.append(SMALL_SAMPLE_SQL_CAVEAT)

    duration = int((time.monotonic() - t0) * 1000)
    return {
        "final_answer": ans,
        "trace": [
            TraceEvent(
                node="verify",
                duration_ms=duration,
                summary=f"Verified findings: {len(ans.findings)} kept, {dropped} dropped.",
            )
        ],
    }


def refuse_node(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Node: refuse unsafe or out-of-scope requests (deterministic, no LLM call)."""
    t0 = time.monotonic()
    plan = state["plan"]
    assert plan is not None

    if plan.intent == Intent.UNSAFE_REQUEST:
        reason = "I can only read review analytics; I can't modify data or reveal internal configuration."
    else:
        games = [g["game"] for g in ctx.meta.get("games", [])]
        games_str = " and ".join(games) if games else "the games"
        reason = f"I answer questions about the player reviews of {games_str}."

    ans = FinalAnswer(summary=reason, findings=[], caveats=[], followups=[])
    duration = int((time.monotonic() - t0) * 1000)

    return {
        "final_answer": ans,
        "refusal_reason": reason,
        "trace": [
            TraceEvent(node="refuse", duration_ms=duration, summary=f"Refused: {plan.intent}.")
        ],
    }
