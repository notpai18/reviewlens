"""Tests for SQL evaluation metrics (spec 12 `test_metrics.py`, SQL part).

EX strict/lenient on crafted result sets: order, float tolerance, NULLs, extra columns.
"""

from __future__ import annotations

from reviewlens.evaluation.metrics_sql import (
    SQLMetrics,
    _find_column_mapping,
    _normalize_value,
    compare_results,
)


def test_identical_results_strict_and_lenient() -> None:
    r = compare_results(["a", "b"], [[1, "x"], [2, "y"]], ["a", "b"], [[1, "x"], [2, "y"]])
    assert r.strict and r.lenient
    assert r.ref_row_count == 2 and r.pred_row_count == 2


def test_unordered_multiset_ignores_row_order() -> None:
    r = compare_results(["a"], [[1], [2], [3]], ["a"], [[3], [1], [2]], ordered=False)
    assert r.strict and r.lenient


def test_ordered_requires_same_order() -> None:
    ref = [[1], [2], [3]]
    assert compare_results(["a"], ref, ["a"], [[1], [2], [3]], ordered=True).strict
    wrong = compare_results(["a"], ref, ["a"], [[3], [2], [1]], ordered=True)
    assert not wrong.strict and not wrong.lenient and not wrong.ordered_match


def test_multiset_counts_duplicates() -> None:
    # same set of distinct rows but different multiplicities must NOT match
    r = compare_results(["a"], [[1], [1], [2]], ["a"], [[1], [2], [2]])
    assert not r.strict and not r.lenient


def test_float_tolerance_two_decimals() -> None:
    r = compare_results(["x"], [[1.234]], ["x"], [[1.2349]])
    assert r.strict
    r2 = compare_results(["x"], [[1.23]], ["x"], [[1.30]])
    assert not r2.strict


def test_nulls_equal_nulls() -> None:
    r = compare_results(["a", "b"], [[1, None]], ["a", "b"], [[1, None]])
    assert r.strict
    assert not compare_results(["a"], [[None]], ["a"], [[0]]).strict


def test_nan_and_inf_normalize_to_null() -> None:
    assert _normalize_value(float("nan")) is None
    assert _normalize_value(float("inf")) is None
    assert _normalize_value(" hi ") == "hi"
    assert _normalize_value(3) == 3
    assert _normalize_value(None) is None


def test_extra_predicted_column_strict_fails_lenient_passes() -> None:
    r = compare_results(
        ["game", "n"],
        [["A", 5], ["B", 7]],
        ["game", "n", "avg_rating"],
        [["A", 5, 4.1], ["B", 7, 3.9]],
    )
    assert not r.strict
    assert r.lenient


def test_lenient_matches_columns_by_name_case_insensitive_any_order() -> None:
    r = compare_results(
        ["game", "n"],
        [["A", 5]],
        ["N", "extra", "GAME"],
        [[5, "zzz", "A"]],
    )
    assert not r.strict
    assert r.lenient


def test_lenient_fails_when_reference_column_missing() -> None:
    r = compare_results(["game", "n"], [["A", 5]], ["game", "other"], [["A", 5]])
    assert not r.lenient


def test_lenient_ordered() -> None:
    ref = [["A", 1], ["B", 2]]
    ok = compare_results(["g", "n"], ref, ["g", "n", "x"], [["A", 1, 0], ["B", 2, 0]], ordered=True)
    bad = compare_results(
        ["g", "n"], ref, ["g", "n", "x"], [["B", 2, 0], ["A", 1, 0]], ordered=True
    )
    assert ok.lenient and not bad.lenient


def test_row_count_mismatch() -> None:
    r = compare_results(["a"], [[1], [2]], ["a"], [[1]])
    assert not r.strict and not r.lenient
    assert (r.ref_row_count, r.pred_row_count) == (2, 1)


def test_fewer_predicted_columns_never_match() -> None:
    r = compare_results(["a", "b"], [[1, 2]], ["a"], [[1]])
    assert not r.strict and not r.lenient


def test_find_column_mapping() -> None:
    assert _find_column_mapping(["a", "b"], ["B", "x", "A"]) == {0: 2, 1: 0}
    assert _find_column_mapping(["a", "z"], ["a"]) is None


def test_sqlmetrics_rates_and_empty() -> None:
    empty = SQLMetrics()
    assert empty.validator_pass_rate == 0.0
    assert empty.first_attempt_rate == 0.0
    assert empty.strict_exec_accuracy == 0.0
    assert empty.lenient_exec_accuracy == 0.0
    assert empty.mean_attempts == 0.0

    m = SQLMetrics(
        total=4,
        validator_pass=3,
        first_attempt_success=2,
        strict_exact=1,
        lenient_exact=2,
        total_attempts=6,
    )
    d = m.to_dict()
    assert d["total"] == 4
    assert d["validator_pass_rate"] == 0.75
    assert d["first_attempt_success_rate"] == 0.5
    assert d["strict_execution_accuracy"] == 0.25
    assert d["lenient_execution_accuracy"] == 0.5
    assert d["mean_attempts"] == 1.5
