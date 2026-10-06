# ReviewLens Evaluation Summary

Generated: 2026-10-06T01:36:02.649542

## SQL Tool

| Metric | Value |
|---|---|
| total | 12 |
| validator_pass_rate | 0.9167 |
| first_attempt_success_rate | 0.9167 |
| strict_execution_accuracy | 0.5 |
| lenient_execution_accuracy | 0.25 |
| mean_attempts | 0.9167 |

## Retrieval Benchmark

| Mode | Overall Hit@5 | Overall MRR | Lexical Hit@5 | Semantic Hit@5 | Mixed Hit@5 |
|---|---|---|---|---|---|
| bm25 | 0.4167 | 0.3701 | 0.95 | 0.0 | 0.3 |
| dense | 0.0333 | 0.025 | 0.1 | 0.0 | 0.0 |
| hybrid_rrf | 0.3667 | 0.221 | 0.85 | 0.0 | 0.25 |

### Bootstrap Confidence Intervals (Hybrid - Baseline Hit@5)

- vs bm25: diff=-0.05, 95% CI [-0.1167, 0.0] (Inconclusive)
- vs dense: diff=0.3333, 95% CI [0.2167, 0.45] (Significant)
## End-to-End Evaluation

| Metric | Value |
|---|---|
| total | 90 |
| routing_accuracy | 0.9556 |
| adversarial_safety_rate | 1.0 |
| citation_validity_rate | 0.4 |
| citation_relevance_rate | 1.0 |
| overall_pass_rate | 0.6889 |
| latency_p50 | 17.482 |
| latency_p95 | 23.34 |
| mean_tokens | 0.0 |

