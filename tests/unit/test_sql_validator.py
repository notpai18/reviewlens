"""Tests for SQL validator (Section 7.2, ≥30 parametrized cases).

Tests written BEFORE running them, per Phase 2 spec rule.
Covers: allowed, rejected, limit enforcement, normalization.
"""

from __future__ import annotations

from reviewlens.sql.validator import validate_sql

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ok(sql: str, max_limit: int = 500) -> None:
    """Assert validate_sql returns ok=True."""
    result = validate_sql(sql, max_limit=max_limit)
    assert result.ok, f"Expected OK but got reasons: {result.reasons}\nSQL: {sql}"
    assert result.normalized_sql is not None


def fail(sql: str, reason_prefix: str | tuple[str, ...], max_limit: int = 500) -> None:
    """Assert validate_sql returns ok=False with a reason starting with reason_prefix.

    reason_prefix can be a tuple to accept any of several valid reason codes —
    e.g. DML statements are rejected at 'not_a_select' (step 4) before step 5
    (forbidden_statement), which is equally correct.
    """
    result = validate_sql(sql, max_limit=max_limit)
    assert not result.ok, f"Expected FAIL but got OK. SQL: {sql}"
    prefixes = (reason_prefix,) if isinstance(reason_prefix, str) else reason_prefix
    assert any(r.startswith(p) for r in result.reasons for p in prefixes), (
        f"Expected reason starting with any of {prefixes}, got {result.reasons}\nSQL: {sql}"
    )


# ===========================================================================
# ALLOWED queries
# ===========================================================================


class TestAllowedQueries:
    def test_plain_select(self):
        ok("SELECT * FROM reviews LIMIT 10")

    def test_select_with_where(self):
        ok("SELECT rating, content FROM reviews WHERE game = 'Brawl Stars'")

    def test_aggregate(self):
        ok("SELECT game, COUNT(*) AS n FROM reviews GROUP BY game")

    def test_having(self):
        ok("SELECT game, AVG(rating) AS avg_r FROM reviews GROUP BY game HAVING COUNT(*) > 100")

    def test_cte(self):
        ok("WITH t AS (SELECT * FROM reviews WHERE rating = 1) SELECT COUNT(*) FROM t")

    def test_window_function(self):
        ok(
            "SELECT review_date, rating, "
            "AVG(rating) OVER (PARTITION BY game ORDER BY review_date) AS rolling "
            "FROM reviews LIMIT 50"
        )

    def test_union_all(self):
        ok(
            "SELECT review_id, rating FROM reviews WHERE game='Brawl Stars' "
            "UNION ALL "
            "SELECT review_id, rating FROM reviews WHERE game='Clash Royale'"
        )

    def test_subquery(self):
        ok(
            "SELECT * FROM (SELECT game, COUNT(*) AS n FROM reviews GROUP BY game) AS s "
            "ORDER BY n DESC"
        )

    def test_ilike(self):
        ok("SELECT COUNT(*) FROM reviews WHERE content ILIKE '%crash%'")

    def test_mixed_case_table_name(self):
        """Case-insensitive table check: REVIEWS should be allowed."""
        ok("SELECT * FROM REVIEWS LIMIT 5")

    def test_date_function(self):
        ok(
            "SELECT DATE_TRUNC('month', review_date) AS m, AVG(rating) "
            "FROM reviews GROUP BY m ORDER BY m"
        )

    def test_case_when(self):
        ok(
            "SELECT CASE WHEN rating >= 4 THEN 'positive' ELSE 'negative' END AS sentiment, "
            "COUNT(*) FROM reviews GROUP BY 1"
        )

    def test_regexp_matches(self):
        ok(
            r"SELECT COUNT(*) FROM reviews "
            r"WHERE regexp_matches(content, '\bads?\b', 'i')"
        )


# ===========================================================================
# REJECTED queries
# ===========================================================================


class TestRejectedQueries:
    def test_drop_table(self):
        # DROP is not a Query → caught at not_a_select (step 4), which is correct
        fail("DROP TABLE reviews", ("forbidden_statement", "not_a_select"))

    def test_delete(self):
        fail("DELETE FROM reviews WHERE rating = 1", ("forbidden_statement", "not_a_select"))

    def test_update(self):
        fail(
            "UPDATE reviews SET rating = 5 WHERE rating = 1",
            ("forbidden_statement", "not_a_select"),
        )

    def test_insert(self):
        fail(
            "INSERT INTO reviews (review_id) VALUES ('x')", ("forbidden_statement", "not_a_select")
        )

    def test_create_table(self):
        fail("CREATE TABLE evil AS SELECT * FROM reviews", ("forbidden_statement", "not_a_select"))

    def test_alter_table(self):
        fail("ALTER TABLE reviews ADD COLUMN evil TEXT", ("forbidden_statement", "not_a_select"))

    def test_multiple_statements(self):
        fail("SELECT 1; DROP TABLE reviews", "multiple_statements")

    def test_comment_hidden_second_statement(self):
        """Semi-colon after comment — still two statements."""
        fail("SELECT 1 -- comment\n; DROP TABLE reviews", "multiple_statements")

    def test_copy(self):
        fail("COPY reviews TO '/tmp/dump.csv'", ("forbidden_statement", "not_a_select"))

    def test_attach(self):
        fail("ATTACH ':memory:' AS evil", ("forbidden_statement", "not_a_select"))

    def test_pragma(self):
        """pragma_ prefix in function name."""
        fail("SELECT * FROM pragma_database_list()", "forbidden_function")

    def test_select_into(self):
        fail("SELECT * INTO backup FROM reviews", "forbidden_statement")

    def test_read_csv_function(self):
        fail("SELECT read_csv('evil.csv') FROM reviews", "forbidden_function")

    def test_read_parquet_function(self):
        fail("SELECT * FROM read_parquet('evil.parquet')", "forbidden_function")

    def test_unknown_table(self):
        fail("SELECT * FROM secrets", "table_not_allowed")

    def test_qualified_table_with_db(self):
        fail("SELECT * FROM other_db.reviews", "qualified_table")

    def test_forbidden_table_in_cte(self):
        """CTE that references a disallowed table in its definition."""
        fail(
            "WITH t AS (SELECT * FROM secrets) SELECT * FROM t",
            "table_not_allowed",
        )

    def test_forbidden_table_in_subquery(self):
        fail(
            "SELECT * FROM (SELECT * FROM secrets) s",
            "table_not_allowed",
        )

    def test_forbidden_table_in_union_branch(self):
        fail(
            "SELECT * FROM reviews UNION ALL SELECT * FROM secrets",
            "table_not_allowed",
        )

    def test_getenv_function(self):
        fail("SELECT getenv('GEMINI_API_KEY')", "forbidden_function")

    def test_current_setting(self):
        fail("SELECT current_setting('some.key')", "forbidden_function")

    def test_unparsable(self):
        fail("BLAH BLAH BLAH @@@", "parse_error")

    def test_empty_string(self):
        fail("", "empty_input")

    def test_only_whitespace(self):
        fail("   ", "empty_input")


# ===========================================================================
# LIMIT enforcement
# ===========================================================================


class TestLimitEnforcement:
    def test_limit_added_when_absent(self):
        result = validate_sql("SELECT * FROM reviews", max_limit=500)
        assert result.ok
        assert "LIMIT 500" in result.normalized_sql

    def test_limit_lowered_when_too_large(self):
        result = validate_sql("SELECT * FROM reviews LIMIT 9999", max_limit=500)
        assert result.ok
        assert "LIMIT 500" in result.normalized_sql

    def test_limit_kept_when_within_bound(self):
        result = validate_sql("SELECT * FROM reviews LIMIT 10", max_limit=500)
        assert result.ok
        assert "LIMIT 10" in result.normalized_sql

    def test_union_wrapped_with_limit(self):
        result = validate_sql(
            "SELECT * FROM reviews WHERE game='A' UNION ALL SELECT * FROM reviews WHERE game='B'",
            max_limit=500,
        )
        assert result.ok
        sql = result.normalized_sql
        assert "LIMIT 500" in sql

    def test_cte_select_gets_limit(self):
        result = validate_sql(
            "WITH t AS (SELECT * FROM reviews) SELECT * FROM t",
            max_limit=200,
        )
        assert result.ok
        assert "LIMIT 200" in result.normalized_sql


# ===========================================================================
# NORMALIZATION
# ===========================================================================


class TestNormalization:
    def test_executed_sql_equals_normalized(self):
        """The normalized_sql must be what we actually execute."""
        raw = "SELECT   *   FROM  reviews  WHERE rating=1   LIMIT 5"
        result = validate_sql(raw)
        assert result.ok
        # Re-validate the normalized form — should still pass
        result2 = validate_sql(result.normalized_sql)
        assert result2.ok

    def test_trailing_semicolon_stripped(self):
        result = validate_sql("SELECT * FROM reviews LIMIT 5;")
        assert result.ok
        assert not result.normalized_sql.endswith(";")

    def test_multiple_trailing_semicolons(self):
        result = validate_sql("SELECT * FROM reviews LIMIT 5;;;")
        assert result.ok

    def test_cte_allowed_name_not_flagged(self):
        """CTE alias 'evil' is fine even though the table 'evil' isn't in allowlist."""
        result = validate_sql(
            "WITH evil AS (SELECT * FROM reviews WHERE rating=1) SELECT COUNT(*) FROM evil"
        )
        assert result.ok
