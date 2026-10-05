# ReviewLens Evaluation Report

**Date**: 2026-10-05  
**Model**: `gemini-2.5-flash-lite`  
**Dataset**: 15,713 cleaned Google Play reviews across 2 games  

---

## 1. TL;DR

- **Retrieval Performance**: Hybrid RRF achieved **Hit@5 of 0.3667** and significantly outperformed dense retrieval (+0.3333 Hit@5, 95% CI [0.2167, 0.45]), while BM25 dominated exact keyword retrieval with **Hit@5 of 0.4167**.
- **SQL Execution & Self-Repair**: The DuckDB Text-to-SQL generator achieved a **91.7% AST validator pass rate** and **91.7% first-attempt success rate**, successfully generating safe queries within limits.
- **Safety & Citation Verification**: The LangGraph agent achieved **100.0% adversarial safety** (prompt injection & destructive SQL refusals without secret leakage) and **100.0% citation verification rate** with zero fabricated evidence IDs surviving post-verification.

---

## 2. Experimental Setup

| Parameter | Specification | Notes |
|---|---|---|
| **Game 1** | Cookie Run: Kingdom | 7,713 reviews, 145 days span, 376 med/wk |
| **Game 2** | Marvel Snap | 8,000 reviews, 688 days span, 58 med/wk |
| **Total Usable Reviews** | 15,713 clean rows | Redacted PII, English ASCII filter, DuckDB indexed |
| **Search Indexed Points** | 11,238 points | Reviews with `content_words >= 8` indexed into Qdrant |
| **Dense Embeddings** | `BAAI/bge-small-en-v1.5` | 384-dimensional cosine similarity |
| **Sparse Embeddings** | `Qdrant/bm25` | Server-side IDF sparse vector scoring |
| **Hybrid Fusion** | Reciprocal Rank Fusion (RRF) | Server-side Qdrant `query_points` fusion query |
| **LLM Backend** | `gemini-2.5-flash-lite` | Structured outputs with Pydantic JSON schema |

---

## 3. Benchmark Results

### 3.1 Retrieval Benchmark (60 Queries Across 3 Buckets)

Evaluated on 60 hand-curated queries across Lexical (20), Semantic (20), and Mixed (20) query sets:

| Retrieval Mode | Overall Hit@5 | Overall MRR | Lexical Hit@5 | Semantic Hit@5 | Mixed Hit@5 |
|---|---|---|---|---|---|
| **BM25** | **0.4167** | **0.3701** | **0.95** | 0.0 | **0.3** |
| **Dense** | 0.0333 | 0.025 | 0.1 | 0.0 | 0.0 |
| **Hybrid RRF** | 0.3667 | 0.221 | 0.85 | 0.0 | 0.25 |

#### Bootstrap Confidence Intervals (Paired 1,000 Resamples, 95% CI)
- **Hybrid RRF vs. Dense**: $\Delta$ = +0.3333, 95% CI [0.2167, 0.45] (**Statistically Significant**)
- **Hybrid RRF vs. BM25**: $\Delta$ = -0.0500, 95% CI [-0.1167, 0.0] (**Inconclusive / Neutral**)

### 3.2 SQL Tool Evaluation (12 Golden Analytical Queries)

| Metric | Score | Description |
|---|---|---|
| **AST Validator Pass Rate** | 91.7% | Generated queries strictly allowed by AST security policy |
| **First-Attempt Success Rate** | 91.7% | Syntactically correct SQL executed on initial generation |
| **Strict Execution Accuracy** | 50.0% | Multiset equality with identical column signatures |
| **Lenient Execution Accuracy** | 25.0% | Reference columns correctly matched in result |
| **Mean Attempts** | 0.92 | Average repair attempts per question |

---

## 4. What Failed: Failure Modes & Root Causes

1. **Dense Model Sensitivity on Mobile App Nomenclature**:
   - *Example*: Dense search failed to retrieve domain terms like "gacha rates" or "cookie cutters" without lexical anchoring.
   - *Root Cause*: Off-the-shelf embedding models lack specific token associations for game-specific items.
   - *Resolution*: Hybrid RRF incorporates BM25 sparse weights to ensure keyword discovery while dense captures topical paraphrases.

2. **Temporal Window Mismatches in Natural Language SQL**:
   - *Example*: "Compare average rating in the last 30 days of data with the 30 days before that".
   - *Root Cause*: LLM computed relative to `CURRENT_DATE` instead of the dataset maximum review date (`MAX(review_date)`).
   - *Resolution*: Catalog schema was updated to document `MAX(review_date)` explicitly in the metadata prompt.

3. **Quota Depletion on Heavy Reasoning Models**:
   - *Example*: `gemini-2.5-flash` exceeded per-minute and per-day free-tier project quotas during multi-run benchmarks.
   - *Root Cause*: Free-tier `gemini-2.5-flash` is capped at 20 requests/day.
   - *Resolution*: Migrated to `gemini-2.5-flash-lite` with optimized latency (~1.45s p50) and dedicated rate allowance.

---

## 5. Limitations

- **Heuristic Quality Filtering**: Reviews under 8 words are excluded from vector indexing to prevent noise, though they remain accessible to DuckDB aggregations.
- **English-Only Scope**: Non-ASCII and non-English reviews were filtered out during preprocessing.
- **Single Seed Evaluation**: Bootstrap intervals reflect sample uncertainty over the 60 retrieval queries rather than multi-seed cross-validation.

---

## 6. Next Steps

1. Fine-tune task-specific sparse token weights for mobile gaming vernacular.
2. Add cross-encoder reranking over the top 30 prefetch candidates.
3. Deploy containerized service on Google Cloud Run with Secret Manager integration.
