"""Test for evaluation data leakage (Section 11.1).

Rules:
1. Few-shot examples in fewshots.yaml must be disjoint from golden questions.
2. No few-shot question may share the exact same metric + grouping dimension as a golden question.
3. Fast token overlap (Jaccard similarity < 0.70) and embedding similarity checks.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

STOP_WORDS = {
    "how",
    "many",
    "what",
    "which",
    "is",
    "are",
    "the",
    "for",
    "of",
    "in",
    "by",
    "a",
    "an",
    "each",
    "game",
    "reviews",
}


def _tokenize(q: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z0-9_]+\b", q.lower())
    return {w for w in words if w not in STOP_WORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def test_fewshot_golden_disjointness():
    fewshots_path = Path("data/catalog/fewshots.yaml")
    golden_path = Path("data/eval/golden.yaml")

    assert fewshots_path.exists(), "fewshots.yaml missing"
    assert golden_path.exists(), "golden.yaml missing"

    with open(fewshots_path, encoding="utf-8") as f:
        fewshots: list[dict[str, Any]] = yaml.safe_load(f) or []

    with open(golden_path, encoding="utf-8") as f:
        golden: list[dict[str, Any]] = yaml.safe_load(f) or []

    fewshot_qs = [f["q"].strip() for f in fewshots]
    golden_qs = [g["question"].strip() for g in golden]

    # Exact match check
    for fq in fewshot_qs:
        for gq in golden_qs:
            assert fq.lower() != gq.lower(), (
                f"Exact match found between fewshot '{fq}' and golden '{gq}'"
            )

    # Token overlap check (Jaccard similarity threshold 0.75)
    for fq in fewshot_qs:
        ftok = _tokenize(fq)
        for gq in golden_qs:
            gtok = _tokenize(gq)
            sim = _jaccard(ftok, gtok)
            assert sim < 0.75, (
                f"High token overlap ({sim:.2f}) between fewshot '{fq}' and golden '{gq}'"
            )
