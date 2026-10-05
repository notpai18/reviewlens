"""Evaluation runner script.

Usage:
    python scripts/run_eval.py --suite sql --runs 1
    python scripts/run_eval.py --suite retrieval
    python scripts/run_eval.py --suite e2e
    python scripts/run_eval.py --suite all

Outputs to eval/results/metrics.json and eval/results/summary.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reviewlens.config import get_settings
from reviewlens.evaluation.metrics_sql import SQLMetrics, compare_results
from reviewlens.models import ValidationResult
from reviewlens.sql.generate import SQLGenerator
from reviewlens.sql.validator import validate_sql
from reviewlens.warehouse.catalog import load_meta_json
from reviewlens.warehouse.duckdb_backend import DuckDBBackend


def _fill_game_names(text: str, games: list[str]) -> str:
    """Replace {G1} and {G2} placeholders with actual game names."""
    g1 = games[0] if games else "Game1"
    g2 = games[1] if len(games) > 1 else "Game2"
    return text.replace("{G1}", g1).replace("{G2}", g2)


def _load_golden(path: Path, games: list[str]) -> list[dict[str, Any]]:
    """Load and fill golden questions."""
    with open(path, encoding="utf-8") as f:
        questions = yaml.safe_load(f) or []
    for q in questions:
        q["question"] = _fill_game_names(q["question"], games)
        if "reference_sql" in q:
            q["reference_sql"] = _fill_game_names(q["reference_sql"], games)
    return questions


async def run_sql_eval(
    generator: SQLGenerator,
    warehouse: DuckDBBackend,
    golden_path: Path,
    games: list[str],
    runs: int = 1,
) -> SQLMetrics:
    """Run SQL evaluation against golden questions."""
    questions = [
        q for q in _load_golden(golden_path, games) if q.get("category") == "sql"
    ]

    metrics = SQLMetrics()
    errors: list[dict[str, Any]] = []

    for q in questions:
        qid = q["id"]
        question = q["question"]
        ref_sql_raw = q.get("reference_sql", "")

        print(f"  [{qid}] {question[:70]}...", flush=True)

        # Validate reference SQL
        ref_validation = validate_sql(ref_sql_raw.strip(), max_limit=10000)
        if not ref_validation.ok:
            print(f"    WARNING: reference SQL failed validation: {ref_validation.reasons}")

        # Get reference result
        ref_result = None
        ref_cols: list[str] = []
        ref_rows: list[list[Any]] = []
        if ref_validation.ok and ref_validation.normalized_sql:
            ref_result = await warehouse.execute(ref_validation.normalized_sql)
            if not ref_result.error:
                ref_cols = ref_result.columns
                ref_rows = ref_result.rows

        for run_num in range(runs):
            metrics.total += 1
            tool_result = await generator.run(question)

            num_attempts = len(tool_result.attempts)
            metrics.total_attempts += num_attempts

            # Validator pass: did the last generated SQL pass validation?
            if any(a.status != "validation_error" for a in tool_result.attempts):
                metrics.validator_pass += 1

            # First attempt success
            if tool_result.attempts and tool_result.attempts[0].status == "success":
                metrics.first_attempt_success += 1

            if not tool_result.success or tool_result.result is None:
                errors.append({
                    "id": qid,
                    "run": run_num + 1,
                    "question": question,
                    "status": "failed",
                    "error": tool_result.error_summary,
                    "attempts": num_attempts,
                })
                continue

            pred_cols = tool_result.result.columns
            pred_rows = tool_result.result.rows

            # Compare against reference
            if ref_cols and ref_rows is not None:
                ordered = "ORDER BY" in ref_sql_raw.upper()
                cmp = compare_results(ref_cols, ref_rows, pred_cols, pred_rows, ordered=ordered)
                if cmp.strict:
                    metrics.strict_exact += 1
                if cmp.lenient:
                    metrics.lenient_exact += 1

                if not cmp.lenient:
                    errors.append({
                        "id": qid,
                        "run": run_num + 1,
                        "question": question,
                        "status": "wrong_result",
                        "strict": cmp.strict,
                        "lenient": cmp.lenient,
                        "ref_rows": len(ref_rows),
                        "pred_rows": len(pred_rows),
                        "attempts": num_attempts,
                    })

    metrics.errors = errors
    return metrics


def run_retrieval_eval(
    queries_path: Path,
    settings: Any,
) -> dict[str, Any]:
    """Run retrieval evaluation for bm25, dense, and hybrid_rrf modes."""
    from reviewlens.agent.factory import build_qdrant
    from reviewlens.evaluation.metrics_retrieval import (
        QueryResult,
        evaluate_retrieval,
        paired_bootstrap_ci,
    )
    from reviewlens.models import Filters
    from reviewlens.search.hybrid import search

    if not queries_path.exists():
        print(f"Error: {queries_path} not found. Run scripts/make_retrieval_queries.py first.")
        return {}

    with open(queries_path, encoding="utf-8") as f:
        queries = yaml.safe_load(f) or []

    client = build_qdrant(settings)
    try:
        if not client.collection_exists(settings.qdrant_collection):
            print(
                f"Collection '{settings.qdrant_collection}' not found in Qdrant at {settings.qdrant_url}. "
                "Run `python scripts/ingest.py --rebuild` first."
            )
            return {}
    except Exception as e:
        print(f"Could not connect to Qdrant at {settings.qdrant_url}: {e}")
        return {}

    modes = ["bm25", "dense", "hybrid_rrf"]
    results_by_mode: dict[str, list[QueryResult]] = {m: [] for m in modes}

    print(f"Evaluating {len(queries)} queries across modes: {modes}...")
    for idx, q in enumerate(queries, 1):
        qid = q.get("id", f"Q{idx}")
        bucket = q["bucket"]
        game = q.get("game", "")
        query_text = q["query"]
        relevant = set(q.get("relevant_review_ids", []))
        flt = Filters(game=game) if game else Filters()

        for m in modes:
            try:
                retrieved = search(
                    client=client,
                    collection=settings.qdrant_collection,
                    query=query_text,
                    mode=m,
                    filters=flt,
                    k=10,
                )
                r_ids = [r.review_id for r in retrieved]
            except Exception as exc:
                print(f"  Error on query {qid} ({m}): {exc}")
                r_ids = []

            results_by_mode[m].append(
                QueryResult(
                    query_id=qid,
                    bucket=bucket,
                    game=game,
                    relevant_ids=relevant,
                    retrieved_ids=r_ids,
                )
            )

    eval_by_mode = {}
    for m in modes:
        eval_by_mode[m] = evaluate_retrieval(m, results_by_mode[m])

    # Bootstrap CIs for hybrid_rrf vs bm25, hybrid_rrf vs dense
    hybrid_hit5 = eval_by_mode["hybrid_rrf"].query_hit5
    bm25_hit5 = eval_by_mode["bm25"].query_hit5
    dense_hit5 = eval_by_mode["dense"].query_hit5

    ci_bm25 = paired_bootstrap_ci(hybrid_hit5, bm25_hit5, baseline_name="bm25")
    ci_dense = paired_bootstrap_ci(hybrid_hit5, dense_hit5, baseline_name="dense")

    out = {
        "modes": {m: metrics.to_dict() for m, metrics in eval_by_mode.items()},
        "bootstrap_ci": [ci_bm25.to_dict(), ci_dense.to_dict()],
    }
    return out


def _write_results(
    metrics_dict: dict[str, Any],
    output_dir: Path,
) -> None:
    """Write metrics.json and summary.md."""
    output_dir.mkdir(parents=True, exist_ok=True)

    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_dict, f, indent=2)
    print(f"\nMetrics written to {metrics_path}")

    summary_path = output_dir / "summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("# ReviewLens Evaluation Summary\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")

        if "sql" in metrics_dict:
            sql_m = metrics_dict["sql"]
            f.write("## SQL Tool\n\n")
            f.write("| Metric | Value |\n|---|---|\n")
            for k, v in sql_m.items():
                if k != "errors":
                    f.write(f"| {k} | {v} |\n")
            f.write("\n")

        if "retrieval" in metrics_dict and "modes" in metrics_dict["retrieval"]:
            ret_m = metrics_dict["retrieval"]["modes"]
            f.write("## Retrieval Benchmark\n\n")
            f.write("| Mode | Overall Hit@5 | Overall MRR | Lexical Hit@5 | Semantic Hit@5 | Mixed Hit@5 |\n")
            f.write("|---|---|---|---|---|---|\n")
            for mode_name, m in ret_m.items():
                ov = m.get("overall", {})
                buckets = m.get("by_bucket", {})
                lex_h5 = buckets.get("lexical", {}).get("hit_at_5", "-")
                sem_h5 = buckets.get("semantic", {}).get("hit_at_5", "-")
                mix_h5 = buckets.get("mixed", {}).get("hit_at_5", "-")
                f.write(
                    f"| {mode_name} | {ov.get('hit_at_5', '-')} | {ov.get('mrr', '-')} | "
                    f"{lex_h5} | {sem_h5} | {mix_h5} |\n"
                )
            f.write("\n### Bootstrap Confidence Intervals (Hybrid - Baseline Hit@5)\n\n")
            for ci in metrics_dict["retrieval"].get("bootstrap_ci", []):
                sig = " (Significant)" if ci.get("significant") else " (Inconclusive)"
                f.write(f"- vs {ci['baseline']}: diff={ci['diff_mean']}, 95% CI [{ci['ci_lower']}, {ci['ci_upper']}]{sig}\n")
            f.write("\n")

    print(f"Summary written to {summary_path}")


async def _async_main(args: argparse.Namespace) -> None:
    settings = get_settings()
    meta = load_meta_json(settings.data_meta_path)
    games = [g["game"] for g in meta.get("games", [])]

    golden_path = Path("data/eval/golden.yaml")
    retrieval_queries_path = Path("data/eval/retrieval_queries.yaml")
    output_dir = Path("eval/results")

    print(f"Games: {games}")
    print(f"Suite: {args.suite}, Runs: {args.runs}")

    metrics_out: dict[str, Any] = {"timestamp": datetime.now().isoformat(), "games": games}

    if args.suite in ("sql", "all"):
        print("\n=== SQL Evaluation ===")

        warehouse = DuckDBBackend(
            db_path=settings.duckdb_path,
            max_rows=settings.sql_max_rows,
            timeout_s=settings.sql_timeout_s,
        )

        # Build LLM. Real metrics require a real model (human task H2: Gemini API key).
        if args.fake_llm:
            print(
                "WARNING: --fake-llm smoke mode. Every question gets the SAME canned SQL. "
                "These numbers are NOT results; do not quote them anywhere."
            )
            from reviewlens.llm.fake import FakeLLM

            llm = FakeLLM(
                default_response='{"sql": "SELECT game, COUNT(*) AS n FROM reviews GROUP BY game LIMIT 500", "assumptions": []}'
            )
            output_dir = Path("eval/results/smoke_fake")
            metrics_out["llm"] = "FakeLLM (smoke test only, NOT real results)"
        elif settings.gemini_api_key:
            from reviewlens.llm.gemini import GeminiLLM

            llm = GeminiLLM(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                rpm_limit=settings.llm_rpm_limit,
                timeout_s=settings.llm_timeout_s,
            )
            metrics_out["llm"] = settings.gemini_model
        else:
            print(
                "ERROR: GEMINI_API_KEY is not set (human task H2). Refusing to produce SQL "
                "eval numbers without a real model. Add the key to .env, or use --fake-llm "
                "to smoke-test the pipeline plumbing only.",
                file=sys.stderr,
            )
            sys.exit(2)

        generator = SQLGenerator(
            llm=llm,
            warehouse=warehouse,
            meta_path=settings.data_meta_path,
            max_attempts=settings.sql_max_attempts,
            max_rows=settings.sql_max_rows,
        )

        sql_metrics = await run_sql_eval(
            generator=generator,
            warehouse=warehouse,
            golden_path=golden_path,
            games=games,
            runs=args.runs,
        )
        metrics_out["sql"] = sql_metrics.to_dict()
        metrics_out["sql"]["errors"] = sql_metrics.errors

        print("\nSQL Metrics:")
        for k, v in sql_metrics.to_dict().items():
            print(f"  {k}: {v}")

    if args.suite in ("retrieval", "all"):
        print("\n=== Retrieval Evaluation ===")
        ret_metrics = run_retrieval_eval(retrieval_queries_path, settings)
        if ret_metrics:
            metrics_out["retrieval"] = ret_metrics
            print("Retrieval Metrics:")
            for m, data in ret_metrics.get("modes", {}).items():
                print(f"  {m}: Hit@5={data['overall']['hit_at_5']}, MRR={data['overall']['mrr']}")
            for ci in ret_metrics.get("bootstrap_ci", []):
                print(f"  CI vs {ci['baseline']}: diff={ci['diff_mean']}, 95% [{ci['ci_lower']}, {ci['ci_upper']}]")

    if args.suite in ("e2e", "all"):
        print("\n=== End-to-End Evaluation ===")
        print("  (E2E eval implemented in Phase 4-5 — skipping for Phase 2)")

    _write_results(metrics_out, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="ReviewLens evaluation runner")
    parser.add_argument(
        "--suite",
        choices=["sql", "retrieval", "e2e", "all"],
        default="sql",
        help="Evaluation suite to run (default: sql)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Number of runs per question (default: 1, use 3 for final numbers)",
    )
    parser.add_argument(
        "--fake-llm",
        action="store_true",
        help="Plumbing smoke test with a canned FakeLLM. Output is NOT real results and goes "
        "to eval/results/smoke_fake/.",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
