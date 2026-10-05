# ReviewLens Design Decisions

This document tracks the key architectural and implementation decisions made during development.

## 1. LangGraph for Agent Orchestration

**Decision**: Use LangGraph for orchestrating the agent workflow instead of custom orchestration or other agent frameworks.

**Rationale**: 
- Provides explicit control flow with clear state management
- Direct SDK calls inside nodes allow for easy testing with fakes
- Structured agent graphs are more maintainable than imperative code
- Good debugging and tracing capabilities

**Alternatives considered**: Custom orchestration, CrewAI, AutoGen

## 2. DuckDB for Analytics Warehouse

**Decision**: Use DuckDB as the analytical database instead of PostgreSQL or BigQuery.

**Rationale**:
- Free and fast for read-only analytical workloads
- Deterministic results across environments
- Easy deployment (single file database)
- Excellent SQL compliance and performance
- Built-in read-only mode for security

**Alternatives considered**: PostgreSQL, BigQuery, SQLite

## 3. Local fastembed Models

**Decision**: Use local fastembed models instead of API-based embedding services.

**Rationale**:
- No API quota limitations
- Reproducible results across runs
- Lower latency for embedding computation
- Cost-effective for high-volume usage
- Offline capability

**Alternatives considered**: OpenAI Embeddings API, Cohere Embed API

## 4. Qdrant with Server-side RRF

**Decision**: Use Qdrant with server-side Reciprocal Rank Fusion for hybrid search.

**Rationale**:
- Hybrid search in a single query operation
- Built-in support for both dense and sparse vectors
- Efficient RRF implementation without client-side merging
- Good performance characteristics and scalability
- Native filtering capabilities

**Alternatives considered**: Weaviate, Pinecone, Elasticsearch

## 5. AST-based SQL Validation

**Decision**: Validate SQL using AST parsing with sqlglot instead of regex-based filtering.

**Rationale**:
- Regex filters are bypassable through SQL obfuscation
- AST parsing catches all syntactic variations
- More maintainable than complex regex patterns
- Better error reporting for debugging
- Future-proof against new SQL injection techniques

**Alternatives considered**: Regex filtering, SQL query rewriting, sandboxing

## 6. Stateless API Design

**Decision**: Design API as stateless with client-managed conversation history.

**Rationale**:
- Works on any Cloud Run instance
- Simpler deployment and scaling
- No session storage requirements
- Better fault tolerance
- Easier load balancing

**Alternatives considered**: Server-side session management, WebSocket connections

## 7. Real Public Data with Derived Labels

**Decision**: Use real Google Play review data with evaluation labels derived by programmatic rules.

**Rationale**:
- More realistic than synthetic data
- Demonstrates real-world applicability
- Honest about evaluation limitations
- Shows handling of noisy, unstructured data
- Better for showcasing data engineering skills

**Alternatives considered**: Synthetic data generation, manually labeled datasets

## 8. BudgetedLLM Wrapper and Hard LLM Caps

**Decision**: Wrap all LLM client interactions in a `BudgetedLLM` proxy per request.

**Rationale**:
- Enforces `AGENT_MAX_LLM_CALLS` across all nodes (planning, SQL generate/repair, docs query building, synthesis)
- Prevents unbounded retries or infinite repair loops from burning API quota
- When budget is exhausted, allows agent nodes to gracefully return partial answers with explicit caveats rather than crashing

**Alternatives considered**: LangChain callback counters, unmetered loop with wall-clock timeout only

## 9. Deterministic UUID5 for Vector Points

**Decision**: Compute Qdrant point IDs as `uuid5(NAMESPACE_URL, f"reviewlens:{review_id}")`.

**Rationale**:
- Qdrant requires point IDs to be valid integers or UUIDs
- Native review IDs from scrapers/stores are string hashes
- UUID5 generates identical IDs across multiple ingestion runs, guaranteeing idempotence and preventing duplicate vectors on re-indexing

**Alternatives considered**: Sequential integer IDs, random UUID4 with mapping table

## 10. AST Outer Row Limit Enforcement and Table Function Rejection

**Decision**: Enforce row limits on the root AST SELECT node and disallow all table-valued/file-reading functions in DuckDB.

**Rationale**:
- Queries containing inner `LIMIT` clauses (e.g. inside subqueries or CTEs) could bypass naive limit checks while the outer query returned millions of rows
- DuckDB supports table functions like `read_csv`, `read_ndjson`, `parquet_metadata`, and `glob` inside `FROM` clauses; these must be rejected by inspecting AST node types (`isinstance(tbl.this, exp.Identifier)`) and checking against a strict table whitelist

**Alternatives considered**: Regex blacklist, database user permission grants (DuckDB is in-process and shares file access)

## 11. Bounded Token-Bucket Rate Limiter with TTL Eviction

**Decision**: Implement an in-memory token-bucket rate limiter with automatic TTL pruning of expired client IPs.

**Rationale**:
- Protects public API endpoints from denial-of-service and runaway Gemini API costs
- Extracts client IP safely from the first hop of `X-Forwarded-For`
- TTL eviction bounds memory usage in long-running processes, preventing memory leaks from ephemeral IPs

**Alternatives considered**: Redis rate limiter (adds operational complexity and cost for a single-instance service), fixed-window counter

---

*This document will be updated as new decisions are made during development.*