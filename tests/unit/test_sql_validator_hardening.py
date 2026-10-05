"""Regression tests for validator gaps found in the Phase 0-5 audit."""

from __future__ import annotations

import pytest

from reviewlens.sql.validator import validate_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_ndjson('x.json')",
        "SELECT * FROM read_xlsx('x.xlsx')",
        "SELECT * FROM parquet_metadata('x.parquet')",
        "SELECT * FROM sniff_csv('x.csv')",
        "SELECT * FROM glob('/*')",
        "SELECT * FROM read_csv_auto('x.csv')",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM pragma_database_list()",
        "SELECT * FROM generate_series(1, 10000000)",
        "SELECT * FROM reviews, read_text('/etc/passwd')",
        "SELECT (SELECT content FROM read_text('/etc/passwd'))",
    ],
)
def test_file_and_table_functions_rejected(sql: str) -> None:
    result = validate_sql(sql)
    assert not result.ok, sql
    assert result.normalized_sql is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM 'data/processed/reviews.parquet'",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM main.reviews",
    ],
)
def test_path_and_system_tables_rejected(sql: str) -> None:
    assert not validate_sql(sql).ok


def test_inner_limit_does_not_hide_missing_outer_cap() -> None:
    result = validate_sql(
        "SELECT * FROM reviews WHERE rating IN (SELECT rating FROM reviews LIMIT 1)",
        max_limit=500,
    )
    assert result.ok
    assert result.normalized_sql is not None
    assert result.normalized_sql.rstrip().endswith("LIMIT 500")


def test_small_outer_limit_kept_even_with_large_inner_limit() -> None:
    result = validate_sql(
        "SELECT * FROM (SELECT * FROM reviews LIMIT 400) AS t LIMIT 5", max_limit=500
    )
    assert result.ok
    assert result.normalized_sql is not None
    assert result.normalized_sql.rstrip().endswith("LIMIT 5")


def test_non_literal_limit_is_capped() -> None:
    result = validate_sql("SELECT * FROM reviews LIMIT (SELECT 100000)", max_limit=500)
    assert result.ok
    assert result.normalized_sql is not None
    # Wrapped as SELECT * FROM (...) AS q LIMIT 500, so the outer cap is a literal.
    assert result.normalized_sql.rstrip().endswith("AS q LIMIT 500")


def test_fetch_first_clause_is_capped() -> None:
    result = validate_sql("SELECT * FROM reviews FETCH FIRST 100000 ROWS ONLY", max_limit=500)
    assert result.ok
    assert result.normalized_sql is not None
    assert result.normalized_sql.rstrip().endswith("LIMIT 500")


def test_legit_queries_still_pass() -> None:
    for sql in [
        "SELECT game, AVG(rating) FROM reviews GROUP BY game",
        "WITH t AS (SELECT * FROM reviews) SELECT COUNT(*) FROM t",
        "SELECT * FROM reviews a JOIN reviews b ON a.review_id = b.review_id",
        "SELECT UNNEST([1, 2, 3])",
    ]:
        assert validate_sql(sql).ok, sql
