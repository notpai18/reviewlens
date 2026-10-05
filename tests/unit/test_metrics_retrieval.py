"""Unit tests for retrieval evaluation metrics and paired bootstrap CIs."""

from reviewlens.evaluation.metrics_retrieval import (
    QueryResult,
    evaluate_retrieval,
    hit_at_k,
    paired_bootstrap_ci,
    reciprocal_rank,
)


def test_hit_at_k():
    assert hit_at_k(["a", "b", "c"], {"a"}, 1) == 1.0
    assert hit_at_k(["a", "b", "c"], {"b"}, 1) == 0.0
    assert hit_at_k(["a", "b", "c"], {"b"}, 2) == 1.0
    assert hit_at_k(["a", "b", "c"], {"z"}, 10) == 0.0
    assert hit_at_k([], {"a"}, 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0
    assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5
    assert reciprocal_rank(["a", "b", "c"], {"c"}) == 1.0 / 3.0
    assert reciprocal_rank(["a", "b", "c"], {"z"}) == 0.0
    assert reciprocal_rank([], {"a"}) == 0.0


def test_evaluate_retrieval_and_buckets():
    results = [
        QueryResult(
            query_id="q1",
            bucket="lexical",
            game="G1",
            relevant_ids={"r1"},
            retrieved_ids=["r1", "r2", "r3"],
        ),
        QueryResult(
            query_id="q2",
            bucket="lexical",
            game="G1",
            relevant_ids={"r2"},
            retrieved_ids=["r9", "r2", "r3"],
        ),
        QueryResult(
            query_id="q3",
            bucket="semantic",
            game="G2",
            relevant_ids={"r3"},
            retrieved_ids=["r9", "r8", "r7", "r6", "r5", "r3"],
        ),
    ]

    metrics = evaluate_retrieval("hybrid_rrf", results)
    assert metrics.mode == "hybrid_rrf"
    assert metrics.overall.total_queries == 3

    # q1: hit@1=1, q2: hit@1=0, q3: hit@1=0 -> hit@1 = 1/3
    assert abs(metrics.overall.hit_at_1 - 1.0 / 3.0) < 1e-4

    # q1: hit@5=1, q2: hit@5=1, q3: hit@5=0 (r3 is rank 6) -> hit@5 = 2/3
    assert abs(metrics.overall.hit_at_5 - 2.0 / 3.0) < 1e-4

    # MRR: q1: 1.0, q2: 0.5, q3: 1/6 -> mean = (1 + 0.5 + 0.166667)/3 = 0.55555
    assert abs(metrics.overall.mrr - (1.0 + 0.5 + 1.0 / 6.0) / 3.0) < 1e-4

    # Buckets
    assert "lexical" in metrics.by_bucket
    assert metrics.by_bucket["lexical"].total_queries == 2
    assert metrics.by_bucket["lexical"].hit_at_1 == 0.5
    assert metrics.by_bucket["lexical"].hit_at_5 == 1.0


def test_paired_bootstrap_reproducible():
    treatment = [1.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0] * 5  # 40 queries
    control = [0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0] * 5  # lower

    ci1 = paired_bootstrap_ci(treatment, control, seed=42)
    ci2 = paired_bootstrap_ci(treatment, control, seed=42)

    assert ci1.diff_mean == ci2.diff_mean
    assert ci1.ci_lower == ci2.ci_lower
    assert ci1.ci_upper == ci2.ci_upper
    assert ci1.diff_mean > 0
    assert ci1.ci_lower > 0  # Significant win
    assert ci1.significant is True


def test_paired_bootstrap_inconclusive():
    # Identical performance
    scores = [1.0, 0.0, 1.0, 0.0, 1.0] * 8
    ci = paired_bootstrap_ci(scores, scores, seed=42)
    assert ci.diff_mean == 0.0
    assert ci.ci_lower <= 0.0 <= ci.ci_upper
    assert ci.significant is False
