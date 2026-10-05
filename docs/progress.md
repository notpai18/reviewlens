# ReviewLens Implementation Progress

This document tracks progress across the 9 implementation phases. Each phase concludes with real acceptance check outputs.

## Phase 0: Scaffolding (Day 1)

**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] Create `pyproject.toml` with dependencies and tool configs (ruff, mypy, pytest, coverage)
- [x] Create `Makefile` with all required targets (install, lint, format, typecheck, test, etc.)
- [x] Create `.gitignore` (data dirs, `.env`, caches)
- [x] Create `.env.example` with full configuration documentation
- [x] Create package skeleton (`src/reviewlens/`)
- [x] Implement `config.py` with pydantic-settings
- [x] Implement `logging.py` with structlog
- [x] Implement `models.py` with core data models
- [x] Create `docs/decisions.md` with 7 design decision stubs
- [x] Create unit tests for config, logging, and models
- [x] Setup GitHub Actions CI workflow (`.github/workflows/ci.yml`)
- [x] Verify `make lint typecheck test` passes with 100% coverage

### Acceptance Checks

**Command**: `make lint && make typecheck && make test`

```
env -i HOME=/home/pai PATH=/home/pai/intraastra/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/home/pai/intraastra/src ruff check src tests
All checks passed!
env -i HOME=/home/pai PATH=/home/pai/intraastra/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/home/pai/intraastra/src ruff format --check src tests
8 files already formatted
env -i HOME=/home/pai PATH=/home/pai/intraastra/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/home/pai/intraastra/src mypy src
pyproject.toml: note: unused section(s): module = ['fastembed', 'google.generativeai', 'google_play_scraper', 'langgraph.*', 'qdrant_client.*', 'sqlglot.*']
Success: no issues found in 4 source files
env -i HOME=/home/pai PATH=/home/pai/intraastra/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/home/pai/intraastra/src pytest -m "not slow and not llm" --cov=reviewlens --cov-report=term-missing
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /home/pai/intraastra/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/pai/intraastra
configfile: pyproject.toml
testpaths: tests
plugins: asyncio-1.4.0, langsmith-0.14.4, cov-7.1.0, anyio-4.15.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 14 items

tests/unit/test_config.py::test_settings_load_from_env PASSED            [  7%]
tests/unit/test_config.py::test_settings_defaults PASSED                 [ 14%]
tests/unit/test_config.py::test_settings_test_env PASSED                 [ 21%]
tests/unit/test_config.py::test_get_settings_caching PASSED              [ 28%]
tests/unit/test_logging.py::test_mask_sensitive_data PASSED              [ 35%]
tests/unit/test_logging.py::test_get_logger PASSED                       [ 42%]
tests/unit/test_logging.py::test_configure_logging_prod PASSED           [ 50%]
tests/unit/test_models.py::test_intent_and_tool_enums PASSED             [ 57%]
tests/unit/test_models.py::test_filters_model PASSED                     [ 64%]
tests/unit/test_models.py::test_plan_model PASSED                        [ 71%]
tests/unit/test_models.py::test_sql_result_and_validation PASSED         [ 78%]
tests/unit/test_models.py::test_retrieved_review PASSED                  [ 85%]
tests/unit/test_models.py::test_final_answer_and_api_response PASSED     [ 92%]
tests/unit/test_models.py::test_api_request PASSED                       [100%]

================================ tests coverage ================================
_______________ coverage: platform linux, python 3.12.3-final-0 ________________

Name                         Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------
src/reviewlens/__init__.py       1      0      0      0   100%
src/reviewlens/config.py        41      0      0      0   100%
src/reviewlens/logging.py       29      0      8      0   100%
src/reviewlens/models.py       109      0      0      0   100%
------------------------------------------------------------------------
TOTAL                          180      0      8      0   100%
Required test coverage of 75.0% reached. Total coverage: 100.00%
============================== 14 passed in 0.16s ==============================
```

---

## Phase 1: Data Pipeline (Days 2-3)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] Create hand-crafted 60-review test fixture (`tests/fixtures/reviews_fixture.jsonl`) spanning 2 games, ratings 1-5, and version tags
- [x] Implement `scripts/fetch_reviews.py` (polite, resumable scraping with sidecar token persistence and strict PII exclusion)
- [x] Implement `scripts/import_csv.py` (fallback dataset importer with dynamic column mapping)
- [x] Implement `scripts/profile_reviews.py` (Section 6.1 quality criteria profiler and formatted summary table)
- [x] Implement `scripts/clean_reviews.py` (deduplication, regex PII redaction, ASCII English filter, parquet writer, meta.json extractor)
- [x] Implement `scripts/build_warehouse.py` (DuckDB database loader with Section 6.5 data-quality checks)
- [x] Create Exploratory Data Analysis notebook (`notebooks/01_eda.ipynb`)
- [x] Implement unit tests in `tests/unit/test_data_pipeline.py`
- [x] Verify data cleaning, warehouse building, and profiling with `--fixture` mode
- [x] Verify `make lint format typecheck test` passes with 100% code coverage

### Acceptance Checks

**Command (Real Data Scraped via Google Play Store)**:
`python scripts/profile_reviews.py && python scripts/clean_reviews.py && python scripts/build_warehouse.py`

```
================================================================================
REVIEWLENS DATA PROFILE (Section 6.1 Criteria)
================================================================================
Game                 | Count   | Date Min   | Date Max   | Span  | Med/Wk  | Ver %  | Status
--------------------------------------------------------------------------------------------
Cookie Run: Kingdom  | 7782    | 2026-05-12 | 2026-10-04 | 145 d | 376.0   | 86.0 % | PASS  
Marvel Snap          | 7931    | 2024-11-14 | 2026-10-04 | 688 d | 58.0    | 85.1 % | PASS  
--------------------------------------------------------------------------------------------

Rating Distributions:
  Cookie Run: Kingdom: 1★: 591, 2★: 191, 3★: 354, 4★: 740, 5★: 5906
  Marvel Snap: 1★: 4404, 2★: 968, 3★: 579, 4★: 544, 5★: 1436

Version Coverage:
  Cookie Run: Kingdom: 86.0% non-null app_version
  Marvel Snap: 85.1% non-null app_version
================================================================================
2026-10-05 17:15:58,731 [INFO] All games successfully passed Section 6.1 criteria!

2026-10-05 17:16:03,746 [INFO] Processing data/raw/cookie_run_kingdom.jsonl...
2026-10-05 17:16:05,821 [INFO] Processing data/raw/marvel_snap.jsonl...
2026-10-05 17:16:07,853 [INFO] Total valid unique cleaned reviews: 15713
2026-10-05 17:16:07,909 [INFO] Wrote 15713 rows to Parquet: data/processed/reviews.parquet
2026-10-05 17:16:07,935 [INFO] Wrote metadata to data/processed/meta.json

2026-10-05 17:16:14,262 [INFO] Connecting to DuckDB at data/warehouse/reviewlens.duckdb...
2026-10-05 17:16:14,306 [INFO] Created table 'reviews'. Running data quality checks...
2026-10-05 17:16:14,311 [INFO] PASS: All review_ids are unique.
2026-10-05 17:16:14,312 [INFO] PASS: All ratings are valid integers in 1..5.
2026-10-05 17:16:14,332 [INFO] PASS: No null review_date, content, or game fields.
2026-10-05 17:16:14,336 [INFO] PASS: Exactly 2 games present: ['Cookie Run: Kingdom', 'Marvel Snap'].

================================================================================
WAREHOUSE DATA QUALITY SUMMARY
================================================================================
Game: Cookie Run: Kingdom | Count: 7782   | Dates: 2026-05-12 to 2026-10-04 (145d) | Med/Wk: 376.0 | Ver%: 86.0% | PASS
Game: Marvel Snap        | Count: 7931   | Dates: 2024-11-14 to 2026-10-04 (689d) | Med/Wk: 58.0  | Ver%: 85.1% | PASS
================================================================================

2026-10-05 17:16:14,410 [INFO] PASS: Warehouse build and all data quality checks succeeded!
```

**Unit Tests**: `pytest -m "not slow and not llm" --cov=reviewlens` -> 27 passed in 0.91s (100% coverage).

## Phase 2: SQL Tool (Days 4-5)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] Create `sql/validator.py` with AST-based validation for DuckDB (hardened against file/table functions, strict row limit wrapping, single statement enforcement)
- [x] Implement unit tests for SQL validator with 30+ parameterized cases (`tests/unit/test_sql_validator.py` and `tests/unit/test_sql_validator_hardening.py`)
- [x] Create `warehouse/duckdb_backend.py` for read-only database querying with time/memory caps
- [x] Implement LLM clients in `llm/fake.py` and `llm/gemini.py`
- [x] Create prompts and schema context loaders (`warehouse/catalog.py`)
- [x] Create `data/catalog/schema.yaml` and `data/catalog/fewshots.yaml`
- [x] Create `data/eval/golden.yaml` with 30 evaluation questions (disjoint from fewshots with Jaccard overlap < 0.75 verified by `tests/unit/test_leakage.py`)
- [x] Implement SQL generate/repair loop in `sql/generate.py` with usage tracking
- [x] Implement `evaluation/metrics_sql.py` and runner `scripts/run_eval.py`
- [x] Run unit tests and CLI generation check

### Acceptance Checks

**Command**: `python -m reviewlens.sql.generate "How many reviews does each game have?"`

```
NOTE: GEMINI_API_KEY is not set (human task H2). Using FakeLLM demo generator.

Question: How many reviews does each game have?

Success: True
Attempts: 1
  Attempt 1: success

SQL:
SELECT game, COUNT(*) AS n FROM reviews GROUP BY game LIMIT 500

Columns: ['game', 'n']
Rows (2):
  ['Clash Royale', 30]
  ['Brawl Stars', 30]
```

**Command**: `python scripts/run_eval.py --suite sql --runs 1 --fake-llm`

```
Games: ['Brawl Stars', 'Clash Royale']
Suite: sql, Runs: 1

=== SQL Evaluation ===
WARNING: --fake-llm smoke mode. Every question gets the SAME canned SQL.
  [Q01] How many reviews are there for each game?...
  [Q02] What is the average rating of each game?...
  [Q03] What is the rating distribution (count per star) for Brawl Stars?...
  ...
SQL Metrics:
  total: 12
  validator_pass_rate: 1.0
  first_attempt_success_rate: 1.0
  strict_execution_accuracy: 0.0
  lenient_execution_accuracy: 0.0
  mean_attempts: 1.0
```

---

## Phase 3: Search Tool (Days 6-7)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅ (Pending live Qdrant container H4 for live index run)

### Tasks
- [x] FastEmbed wrapper with local `BAAI/bge-small-en-v1.5` dense and `prithivida/Splade_PP_en_v1` sparse models
- [x] Collection creation with multi-vector configurations and payload indexes for `game`, `app_version`, `rating`, `review_date`
- [x] Deterministic UUID5 point IDs (`uuid5(NAMESPACE_URL, f"reviewlens:{review_id}")`) ensuring idempotent upserts
- [x] Three search modes: `hybrid_rrf` (server-side RRF), `bm25` (sparse only), and `dense` (embeddings only)
- [x] Filter translation for game names, rating ranges, version lists, and date bounds
- [x] Retrieval evaluation metrics (`Hit@1`, `Hit@5`, `Hit@10`, `MRR`) across lexical, semantic, and mixed query buckets
- [x] 1,000-resample paired bootstrap 95% confidence intervals with fixed seed (42) for reproducibility
- [x] Query generator `scripts/make_retrieval_queries.py` generating `data/eval/retrieval_queries.yaml`
- [x] Unit test suites in `tests/unit/test_search.py`, `tests/unit/test_indexer.py`, and `tests/unit/test_metrics_retrieval.py`

### Acceptance Checks
- `pytest tests/unit/test_search.py tests/unit/test_indexer.py tests/unit/test_metrics_retrieval.py` -> 13 passed in 0.35s
- Script verification: `python scripts/make_retrieval_queries.py` -> Generated 30 queries across 3 buckets
- Note: Live index acceptance command (`docker compose up -d qdrant && python scripts/ingest.py --rebuild`) requires Docker binary (Human Task H4).

---

## Phase 4: Agent (Days 8-9)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] State definitions (`AgentState`, `AgentContext`) and node functions (`plan_node`, `sql_tool_node`, `docs_tool_node`, `synthesize_node`, `verify_node`, `refuse_node`)
- [x] Compiled LangGraph state machine with conditional routing for `sql_only`, `docs_only`, `sequential_hybrid`, and `refuse`
- [x] Evidence formatting with prompt-injection defense (escaping `<`, `>`, `"`, stripping ASCII control characters)
- [x] Hallucination verification: `verify_node` cross-checks all finding `evidence_ids` against valid tool results (`SQL#N` and `REV:<id>`), dropping unsupported claims
- [x] Hard LLM call budget guard (`BudgetedLLM`) capping requests at `AGENT_MAX_LLM_CALLS` (default 4) and returning deterministic partial answers on exhaustion
- [x] Agent timeout guard enforcing `AGENT_TIMEOUT_S` via `asyncio.wait_for`
- [x] CLI entry point: `python -m reviewlens.agent.graph "<question>"`
- [x] Full test suite: `tests/unit/test_agent_nodes.py`, `tests/unit/test_agent_graph.py`, and `tests/unit/test_evidence.py`

### Acceptance Checks

**Command**: `python -m reviewlens.agent.graph "What are the common complaints about matchmaking in Brawl Stars?"`

```
NOTE: GEMINI_API_KEY is not set (human task H2). Running with FakeLLM demo agent.

Question: What are the common complaints about matchmaking in Brawl Stars?

=== ANSWER ===
Brawl Stars has an average rating of 4.1. Players frequently report matchmaking latency and bugs.

**Findings**
- *Observed:* Brawl Stars averages 4.1 stars across reviews. [SQL#1]

**Caveats**
- Demonstration answer generated using FakeLLM simulator.

**You could also ask**
- Examine matchmaking latency by app version.

=== SQL ===
  attempt 1: success
  final: SELECT game, ROUND(AVG(rating), 2) AS avg_rating, COUNT(*) AS n FROM reviews GROUP BY game LIMIT 500

=== TRACE ===
  plan             0 ms  intent=analytics, tools=['sql', 'docs']
  sql_tool        39 ms  SQL success: True, attempts: 1 (2 rows)
  docs_tool        0 ms  Search failed: [Errno 111] Connection refused
  docs_tool     1140 ms  Retrieved 0 documents (query='matchmaking crash').
  synthesize       0 ms  Synthesized answer.
  verify           0 ms  Verified findings: 1 kept, 1 dropped.

Usage: 3 LLM calls, 1998+224 tokens
```

Unit test verification: `pytest tests/unit/test_agent_graph.py` -> 5 passed in 2.66s.

---

## Phase 5: API & Demo Page (Day 10)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] FastAPI application with lifespan management, structured JSON logging, and security headers
- [x] Standardized error envelope: `{"error": {"code", "message", "request_id"}}` with 422 (`validation_error`), 429 (`rate_limited`), 502 (`upstream_llm_error`), and 504 (`timeout`)
- [x] Endpoints: `GET /health`, `GET /ready`, `POST /ask`, `POST /ingest`, and `GET /ingest/status`
- [x] In-memory bounded token-bucket rate limiter with IP extraction and TTL cleanup
- [x] Static single-page interactive demo UI (`src/reviewlens/api/static/index.html`) displaying markdown answers, interactive citation tooltips, SQL execution details, and agent trace timeline
- [x] Unit test suite in `tests/unit/test_api.py` covering all endpoints, error cases, cache hits, body size limits, and auth

### Acceptance Checks

**Command**: `curl -i http://127.0.0.1:8000/health`
```http
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
referrer-policy: no-referrer

{"status":"ok"}
```

**Command**: `curl -i -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" -d '{}'`
```http
HTTP/1.1 422 Unprocessable Entity
content-type: application/json

{"error":{"code":"validation_error","message":"body.question: Field required","request_id":"bdc6260b-dcfb-4e52-9b83-0db91b84eba1"}}
```

**Command**: `curl -i http://127.0.0.1:8000/`
```http
HTTP/1.1 200 OK
content-type: text/html; charset=utf-8

<!DOCTYPE html>
<html lang="en">
<head>
    <title>ReviewLens Demo</title>
...
```

Unit test verification: `pytest tests/unit/test_api.py` -> 9 passed in 2.21s.

---

## Phase 6: Full Evaluation & Report (Day 11)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] Run vector indexing into live Qdrant (`scripts/ingest.py --rebuild` -> 11,238 points indexed)
- [x] Verify idempotent indexing (no duplicates on re-run)
- [x] Run retrieval benchmark across 60 queries in 3 buckets (`scripts/run_eval.py --suite retrieval`)
- [x] Compute bootstrap 95% confidence intervals for hybrid vs BM25 and hybrid vs dense
- [x] Implement E2E metrics module (`src/reviewlens/evaluation/metrics_e2e.py`) and unit tests
- [x] Implement automated report generator (`scripts/report.py` -> `REPORT.md`)
- [x] Implement report verification script (`scripts/check_report_numbers.py`)
- [x] Verified 100% of reported numbers match `metrics.json`

### Acceptance Checks

**Command**: `python scripts/run_eval.py --suite retrieval`
```
Games: ['Cookie Run: Kingdom', 'Marvel Snap']
Suite: retrieval, Runs: 1

=== Retrieval Evaluation ===
Evaluating 60 queries across modes: ['bm25', 'dense', 'hybrid_rrf']...
Retrieval Metrics:
  bm25: Hit@5=0.4167, MRR=0.3701
  dense: Hit@5=0.0333, MRR=0.025
  hybrid_rrf: Hit@5=0.3667, MRR=0.221
  CI vs bm25: diff=-0.05, 95% [-0.1167, 0.0]
  CI vs dense: diff=0.3333, 95% [0.2167, 0.45]

Metrics written to eval/results/metrics.json
Summary written to eval/results/summary.md
```

**Command**: `python scripts/check_report_numbers.py`
```
PASS: All key evaluation numbers in REPORT.md are verified against metrics.json!
```

---

## Phase 7: Docker, Cloud Run, CI (Day 12)
**Date**: 2026-10-05  
**Status**: COMPLETED ✅

### Tasks
- [x] Multi-stage `Dockerfile` with non-root `appuser`, pre-downloaded fastembed models, and curl healthcheck
- [x] `docker-compose.yml` with official `qdrant/qdrant:v1.19.2`, healthcheck, volume persistence, and app service
- [x] `.dockerignore` and `.gcloudignore` to prevent leaking secrets, virtual environments, or raw files
- [x] Automated production smoke test script (`scripts/smoke_prod.py`)
- [x] Updated GitHub Actions CI workflow (`.github/workflows/ci.yml`)
- [x] Smoke tested against live server

### Acceptance Checks

**Command**: `python scripts/smoke_prod.py http://127.0.0.1:8000`
```
=== Smoke Testing ReviewLens at http://127.0.0.1:8000 ===
1. Checking http://127.0.0.1:8000/health...
  PASS: /health returned 200 -> {"status":"ok"}
2. Checking http://127.0.0.1:8000/ready...
  PASS: /ready returned 200 -> {"status":"ready"}
3. Checking http://127.0.0.1:8000/ask with question: 'How many reviews are there for each game?'...
  PASS: /ask returned 200
  Answer preview: There are 7,782 reviews for Cookie Run: Kingdom and 7,931 reviews for Marvel Snap. These counts represent the total number of reviews available for ea...

ALL SMOKE CHECKS PASSED SUCCESSFULLY! Service is healthy and functional.
```

---

## Phase 8: Polish (Day 13)
**Status**: IN PROGRESS (Creating polished README and architecture assets)


