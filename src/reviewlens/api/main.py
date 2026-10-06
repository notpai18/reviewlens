"""ReviewLens FastAPI application."""

from __future__ import annotations

import asyncio
import hmac
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from cachetools import TTLCache
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from reviewlens.agent.evidence import render_answer_markdown
from reviewlens.agent.factory import LLMUnavailableError
from reviewlens.agent.graph import run_question
from reviewlens.agent.nodes import AgentContext
from reviewlens.api.citations import split_evidence
from reviewlens.api.deps import get_agent_ctx, get_settings_dep, init_deps
from reviewlens.api.rate_limit import check_rate_limit
from reviewlens.api.schemas import (
    AskRequest,
    AskResponse,
    ErrorResponse,
    IngestRequest,
    IngestStatusResponse,
    SQLInfo,
)
from reviewlens.config import Settings
from reviewlens.llm.gemini import LLMOutputError
from reviewlens.logging import get_logger
from reviewlens.search.ingest import rebuild_index

logger = get_logger()

# Query response cache: 1 hour TTL, up to 1000 items (cached only when history is empty)
query_cache: TTLCache[str, Any] = TTLCache(maxsize=1000, ttl=3600)

# Global ingestion state
_ingest_state: dict[str, Any] = {
    "status": "idle",
    "indexed_count": None,
    "error": None,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    init_deps(get_settings_dep())
    yield


app = FastAPI(title="ReviewLens API", version="1.0.0", lifespan=lifespan)


def _get_request_id() -> str:
    ctx = structlog.contextvars.get_contextvars()
    return str(ctx.get("request_id") or uuid.uuid4())


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    req_id = _get_request_id()
    msg = "; ".join(
        f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": msg, "request_id": req_id}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    req_id = _get_request_id()
    code: str = str(exc.detail) if isinstance(exc.detail, str) else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail), "request_id": req_id}},
    )


@app.middleware("http")
async def security_and_logging_middleware(request: Request, call_next: Any) -> Any:
    # 8 KB body size limit
    if request.headers.get("content-length"):
        try:
            length = int(request.headers["content-length"])
            if length > 8192:
                req_id = str(uuid.uuid4())
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": "Payload exceeds 8 KB limit",
                            "request_id": req_id,
                        }
                    },
                )
        except ValueError:
            pass

    request_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id)

    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
async def ready(ctx: AgentContext = Depends(get_agent_ctx)) -> Any:
    """Readiness probe: DuckDB SELECT 1 works and Qdrant collection is non-empty."""
    try:
        sql_res = await ctx.warehouse.execute("SELECT 1")
        if sql_res.error:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": f"duckdb_error: {sql_res.error}"},
            )

        count = await asyncio.to_thread(
            lambda: (
                ctx.qdrant_client.count(
                    collection_name=ctx.settings.qdrant_collection, exact=True
                ).count
            )
        )
        if count > 0:
            return {"status": "ready"}
        return JSONResponse(
            status_code=503, content={"status": "not_ready", "reason": "empty_collection"}
        )
    except Exception as e:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "error", "reason": str(e)})


@app.post(
    "/ask",
    response_model=AskResponse,
    responses={
        429: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def ask(
    req: AskRequest,
    request: Request,
    ctx: AgentContext = Depends(get_agent_ctx),
    _: Any = Depends(check_rate_limit),
) -> Any:
    req_id = _get_request_id()

    cache_key = req.question.strip().lower()
    if not req.history and cache_key in query_cache:
        cached_resp = query_cache[cache_key]
        cached_resp.request_id = req_id
        cached_resp.cached = True
        return cached_resp

    try:
        raw_history = [{"role": h.role, "content": h.content} for h in req.history]
        result = await run_question(ctx, req.question, raw_history)

        ans = result.get("final_answer")
        ans_markdown = render_answer_markdown(ans) if ans else "Processing failed."

        sql_infos: list[SQLInfo] = []

        sql_res = result.get("sql_result")
        if sql_res and sql_res.result:
            r = sql_res.result
            preview = r.rows[:3]
            sql_infos.append(
                SQLInfo(
                    query=r.sql,
                    status="success" if sql_res.success else "error",
                    attempts=len(sql_res.attempts),
                    columns=r.columns,
                    rows_preview=preview,
                )
            )

        docs = result.get("docs_result") or []
        citations, retrieved_infos = split_evidence(ans, sql_res, docs)

        resp = AskResponse(
            request_id=req_id,
            answer_markdown=ans_markdown,
            answer=ans,
            citations=citations,
            sql=sql_infos,
            retrieved=retrieved_infos,
            trace=result.get("trace") if req.options.include_trace else None,
            usage=result.get("usage"),
            cached=False,
        )

        if not req.history:
            query_cache[cache_key] = resp

        return resp

    except TimeoutError:
        logger.warning("Request timed out", question=req.question[:80])
        return JSONResponse(
            status_code=504,
            content={
                "error": {
                    "code": "timeout",
                    "message": f"Agent request timed out after {ctx.settings.agent_timeout_s}s",
                    "request_id": req_id,
                }
            },
        )
    except (LLMUnavailableError, LLMOutputError) as exc:
        logger.error("Upstream LLM error", error=str(exc))
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "upstream_llm_error",
                    "message": str(exc),
                    "request_id": req_id,
                }
            },
        )
    except Exception as exc:
        logger.exception("Error processing ask request")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": f"Internal Server Error: {exc}",
                    "request_id": req_id,
                }
            },
        )


def _do_background_ingest(ctx: AgentContext, parquet_path: Path) -> None:
    global _ingest_state
    _ingest_state["status"] = "running"
    _ingest_state["error"] = None
    try:
        count = rebuild_index(
            ctx.qdrant_client,
            ctx.settings.qdrant_collection,
            parquet_path,
            recreate=True,
        )
        _ingest_state["status"] = "completed"
        _ingest_state["indexed_count"] = count
    except Exception as exc:
        logger.exception("Background ingestion failed")
        _ingest_state["status"] = "failed"
        _ingest_state["error"] = str(exc)


def _verify_ingest_auth(request: Request, settings: Settings) -> None:
    api_key = settings.ingest_api_key or ""
    if not api_key:
        raise HTTPException(status_code=403, detail="ingest_disabled")
    provided_key = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(provided_key.encode("utf-8"), api_key.encode("utf-8")):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/ingest")
async def ingest(
    req: IngestRequest,
    request: Request,
    bg_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings_dep),
    ctx: AgentContext = Depends(get_agent_ctx),
) -> Any:
    _verify_ingest_auth(request, settings)

    parquet_path = Path("data/processed/reviews.parquet")
    if not parquet_path.exists():
        raise HTTPException(status_code=409, detail="parquet_file_missing")

    bg_tasks.add_task(_do_background_ingest, ctx, parquet_path)
    return {"status": "started"}


@app.get("/ingest/status", response_model=IngestStatusResponse)
async def ingest_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> Any:
    _verify_ingest_auth(request, settings)
    return _ingest_state


# Serve static demo page
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
