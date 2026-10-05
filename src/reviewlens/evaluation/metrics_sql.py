"""SQL evaluation metrics (Section 11.3).

Execution accuracy: compares predicted vs reference SQL results as multisets.
- Strict: same column count required.
- Lenient: extra predicted columns allowed if all reference columns present.

Also reports: validator pass rate, first-attempt success rate, mean attempts.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------


def _normalize_value(v: Any) -> Any:
    """Normalize a cell value for comparison.

    - Floats rounded to 2 decimals
    - None/NULL equal to None
    - Dates as ISO strings (already converted by DuckDBBackend)
    - Strings: strip whitespace
    """
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return round(v, 2)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return v.strip()
    return v


def _normalize_row(row: list[Any]) -> tuple[Any, ...]:
    return tuple(_normalize_value(v) for v in row)


# ---------------------------------------------------------------------------
# Column matching for lenient mode
# ---------------------------------------------------------------------------


def _find_column_mapping(ref_cols: list[str], pred_cols: list[str]) -> dict[int, int] | None:
    """Try to map each reference column index to a predicted column index.

    Uses lowercase column names. Returns None if mapping is impossible.
    """
    ref_lower = [c.lower() for c in ref_cols]
    pred_lower = [c.lower() for c in pred_cols]

    mapping: dict[int, int] = {}
    for ref_idx, ref_col in enumerate(ref_lower):
        if ref_col in pred_lower:
            mapping[ref_idx] = pred_lower.index(ref_col)
        else:
            return None
    return mapping


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------


@dataclass
class ExecAccResult:
    """Result of execution accuracy comparison."""

    strict: bool  # exact column count + same multiset
    lenient: bool  # lenient column matching + same multiset
    ordered_match: bool  # ordered list match (for ORDER BY queries)
    ref_row_count: int
    pred_row_count: int
    note: str = ""


def compare_results(
    ref_cols: list[str],
    ref_rows: list[list[Any]],
    pred_cols: list[str],
    pred_rows: list[list[Any]],
    ordered: bool = False,
) -> ExecAccResult:
    """Compare predicted SQL result against reference result.

    Args:
        ref_cols: Reference column names.
        ref_rows: Reference rows (from reference SQL execution).
        pred_cols: Predicted column names.
        pred_rows: Predicted rows (from predicted SQL execution).
        ordered: If True, compare as ordered list (reference SQL has ORDER BY).

    Returns:
        ExecAccResult with strict, lenient, ordered_match flags.
    """
    # Normalize
    ref_normalized = [_normalize_row(r) for r in ref_rows]
    pred_normalized = [_normalize_row(r) for r in pred_rows]

    # --- Strict mode: same column count, same multiset ---
    strict = False
    if len(ref_cols) == len(pred_cols):
        if ordered:
            strict = ref_normalized == pred_normalized
        else:
            strict = Counter(ref_normalized) == Counter(pred_normalized)

    # --- Lenient mode: extra columns allowed, reference columns must match ---
    lenient = False
    if len(pred_cols) >= len(ref_cols):
        mapping = _find_column_mapping(ref_cols, pred_cols)
        if mapping is not None:
            # Project predicted rows to reference column order
            projected = [
                tuple(_normalize_value(row[mapping[i]]) for i in range(len(ref_cols)))
                for row in pred_rows
            ]
            ref_tuples = ref_normalized
            if ordered:
                lenient = ref_tuples == projected
            else:
                lenient = Counter(ref_tuples) == Counter(projected)

    # --- Ordered comparison (explicit) ---
    ordered_match = False
    if ordered and len(ref_cols) == len(pred_cols):
        ordered_match = ref_normalized == pred_normalized
    elif not ordered:
        ordered_match = strict  # same as strict for unordered

    return ExecAccResult(
        strict=strict,
        lenient=lenient,
        ordered_match=ordered_match,
        ref_row_count=len(ref_rows),
        pred_row_count=len(pred_rows),
    )


# ---------------------------------------------------------------------------
# Aggregate metrics
# ---------------------------------------------------------------------------


@dataclass
class SQLMetrics:
    """Aggregated SQL evaluation metrics."""

    total: int = 0
    validator_pass: int = 0
    first_attempt_success: int = 0
    strict_exact: int = 0
    lenient_exact: int = 0
    total_attempts: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def validator_pass_rate(self) -> float:
        return self.validator_pass / self.total if self.total else 0.0

    @property
    def first_attempt_rate(self) -> float:
        return self.first_attempt_success / self.total if self.total else 0.0

    @property
    def strict_exec_accuracy(self) -> float:
        return self.strict_exact / self.total if self.total else 0.0

    @property
    def lenient_exec_accuracy(self) -> float:
        return self.lenient_exact / self.total if self.total else 0.0

    @property
    def mean_attempts(self) -> float:
        return self.total_attempts / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "validator_pass_rate": round(self.validator_pass_rate, 4),
            "first_attempt_success_rate": round(self.first_attempt_rate, 4),
            "strict_execution_accuracy": round(self.strict_exec_accuracy, 4),
            "lenient_execution_accuracy": round(self.lenient_exec_accuracy, 4),
            "mean_attempts": round(self.mean_attempts, 4),
        }
