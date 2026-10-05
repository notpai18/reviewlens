"""Retrieval evaluation metrics (Section 11.3).

Computes Hit@1, Hit@5, Hit@10, and MRR (Mean Reciprocal Rank) overall and per bucket
(lexical, semantic, mixed). Computes paired bootstrap 95% confidence intervals (1,000
resamples, seed 42) for the difference in Hit@5 between hybrid_rrf and each baseline.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class QueryResult:
    query_id: str
    bucket: str  # "lexical" | "semantic" | "mixed"
    game: str
    relevant_ids: set[str]
    retrieved_ids: list[str]  # ranked list of retrieved review_ids


@dataclass
class BucketMetrics:
    total_queries: int = 0
    hit_at_1: float = 0.0
    hit_at_5: float = 0.0
    hit_at_10: float = 0.0
    mrr: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_queries": self.total_queries,
            "hit_at_1": round(self.hit_at_1, 4),
            "hit_at_5": round(self.hit_at_5, 4),
            "hit_at_10": round(self.hit_at_10, 4),
            "mrr": round(self.mrr, 4),
        }


@dataclass
class BootstrapCI:
    baseline: str
    metric: str
    diff_mean: float
    ci_lower: float
    ci_upper: float
    significant: bool  # True if 0 is not in [ci_lower, ci_upper]

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "metric": self.metric,
            "diff_mean": round(self.diff_mean, 4),
            "ci_lower": round(self.ci_lower, 4),
            "ci_upper": round(self.ci_upper, 4),
            "significant": self.significant,
        }


@dataclass
class RetrievalMetrics:
    mode: str
    overall: BucketMetrics = field(default_factory=BucketMetrics)
    by_bucket: dict[str, BucketMetrics] = field(default_factory=dict)
    query_hit5: list[float] = field(default_factory=list)  # for paired bootstrap

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "overall": self.overall.to_dict(),
            "by_bucket": {b: m.to_dict() for b, m in self.by_bucket.items()},
        }


def hit_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Return 1.0 if any relevant item is in the top-k retrieved items, else 0.0."""
    top_k = retrieved[:k]
    return 1.0 if any(item in relevant for item in top_k) else 0.0


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    """Return 1 / rank of the first relevant item (1-indexed), or 0.0 if not found."""
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def compute_metrics_for_results(results: list[QueryResult]) -> BucketMetrics:
    """Calculate average Hit@1, Hit@5, Hit@10 and MRR over a list of query results."""
    if not results:
        return BucketMetrics()

    n = len(results)
    h1 = sum(hit_at_k(r.retrieved_ids, r.relevant_ids, 1) for r in results) / n
    h5 = sum(hit_at_k(r.retrieved_ids, r.relevant_ids, 5) for r in results) / n
    h10 = sum(hit_at_k(r.retrieved_ids, r.relevant_ids, 10) for r in results) / n
    mrr = sum(reciprocal_rank(r.retrieved_ids, r.relevant_ids) for r in results) / n

    return BucketMetrics(
        total_queries=n,
        hit_at_1=h1,
        hit_at_5=h5,
        hit_at_10=h10,
        mrr=mrr,
    )


def evaluate_retrieval(mode: str, results: list[QueryResult]) -> RetrievalMetrics:
    """Compute overall and per-bucket metrics for a single retrieval mode."""
    overall = compute_metrics_for_results(results)

    grouped: dict[str, list[QueryResult]] = defaultdict(list)
    for r in results:
        grouped[r.bucket].append(r)

    by_bucket: dict[str, BucketMetrics] = {}
    for bucket, b_results in grouped.items():
        by_bucket[bucket] = compute_metrics_for_results(b_results)

    query_hit5 = [hit_at_k(r.retrieved_ids, r.relevant_ids, 5) for r in results]

    return RetrievalMetrics(
        mode=mode,
        overall=overall,
        by_bucket=by_bucket,
        query_hit5=query_hit5,
    )


def paired_bootstrap_ci(
    treatment_scores: list[float],
    control_scores: list[float],
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 42,
    baseline_name: str = "control",
    metric_name: str = "Hit@5",
) -> BootstrapCI:
    """Paired bootstrap confidence interval for difference in scores (treatment - control).

    Seed is fixed to 42 for exact reproducibility.
    """
    assert len(treatment_scores) == len(control_scores), "Scores must be paired"
    n = len(treatment_scores)
    if n == 0:
        return BootstrapCI(baseline_name, metric_name, 0.0, 0.0, 0.0, False)

    rng = np.random.default_rng(seed)
    diffs = np.array(treatment_scores) - np.array(control_scores)
    mean_diff = float(np.mean(diffs))

    # Bootstrap resample the paired differences
    indices = rng.integers(0, n, size=(n_resamples, n))
    bootstrap_means = np.mean(diffs[indices], axis=1)

    low_pct = 100.0 * (alpha / 2.0)
    high_pct = 100.0 * (1.0 - alpha / 2.0)
    ci_lower = float(np.percentile(bootstrap_means, low_pct))
    ci_upper = float(np.percentile(bootstrap_means, high_pct))

    # Significant if 0 is not in the confidence interval
    significant = (ci_lower > 0.0) or (ci_upper < 0.0)

    return BootstrapCI(
        baseline=baseline_name,
        metric=metric_name,
        diff_mean=mean_diff,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        significant=significant,
    )
