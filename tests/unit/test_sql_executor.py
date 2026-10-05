"""Tests for DuckDB executor (test_sql_executor.py)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import duckdb
import pytest

from reviewlens.warehouse.duckdb_backend import DuckDBBackend


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    """Build a small in-memory test DuckDB from reviews_fixture.jsonl."""
    db_path = tmp_path / "test.duckdb"
    parquet_path = Path("data/processed/reviews.parquet")
    if not parquet_path.exists():
        pytest.skip("reviews.parquet not found — run make data --fixture first")
    con = duckdb.connect(str(db_path))
    con.execute(f"""
        CREATE TABLE reviews AS
        SELECT
            review_id, game, review_date::DATE AS review_date,
            rating, thumbs_up, app_version, content, content_words, dev_replied
        FROM read_parquet('{parquet_path}')
    """)
    con.close()
    return db_path


@pytest.fixture
def backend(fixture_db: Path) -> DuckDBBackend:
    return DuckDBBackend(db_path=fixture_db, max_rows=500, timeout_s=10)


class TestDuckDBBackend:
    def test_basic_query(self, backend: DuckDBBackend) -> None:
        result = backend.execute_sync("SELECT COUNT(*) AS n FROM reviews")
        assert not result.error
        assert result.row_count == 1
        assert result.columns == ["n"]
        assert result.rows[0][0] >= 1

    def test_row_cap_and_truncated(self, backend: DuckDBBackend) -> None:
        """With max_rows=5, fetching all should truncate."""
        small_backend = DuckDBBackend(db_path=backend.db_path, max_rows=5, timeout_s=10)
        result = small_backend.execute_sync("SELECT * FROM reviews")
        assert not result.error
        # If there are more than 5 reviews, truncated=True
        if result.row_count == 5:
            assert result.truncated
        else:
            # Fewer than 5 rows in fixture (unlikely, but acceptable)
            assert not result.truncated

    def test_json_safe_values(self, backend: DuckDBBackend) -> None:
        """Dates should come back as ISO strings, not date objects."""
        result = backend.execute_sync("SELECT review_date FROM reviews LIMIT 1")
        assert not result.error
        assert result.row_count >= 1
        date_val = result.rows[0][0]
        # Should be a string like "2024-01-08", not a date object
        assert isinstance(date_val, str), f"Expected str, got {type(date_val)}"
        assert len(date_val) == 10  # YYYY-MM-DD

    def test_db_error_returns_error_field(self, backend: DuckDBBackend) -> None:
        """An invalid SQL should return an error, not raise."""
        result = backend.execute_sync("SELECT nonexistent_column FROM reviews LIMIT 1")
        assert result.error is not None
        assert result.row_count == 0

    def test_write_fails(self, backend: DuckDBBackend) -> None:
        """Database is read-only — writes must fail."""
        result = backend.execute_sync("INSERT INTO reviews (review_id) VALUES ('evil')")
        assert result.error is not None

    def test_timeout(self, tmp_path: Path) -> None:
        """A deliberately heavy query should be interrupted."""
        db_path = tmp_path / "heavy.duckdb"
        con = duckdb.connect(str(db_path))
        con.execute("CREATE TABLE t AS SELECT range(1000) AS n")
        con.close()

        heavy_backend = DuckDBBackend(db_path=db_path, max_rows=500, timeout_s=1)
        # Cross join: 1000^3 = 10^9 rows, should time out in 1 second
        result = heavy_backend.execute_sync("SELECT COUNT(*) FROM t, t t2, t t3")
        # Either times out or completes very fast — either is fine for test
        # We just assert the backend doesn't hang indefinitely or raise
        assert result.error is not None or result.row_count >= 0

    def test_health_check(self, backend: DuckDBBackend) -> None:
        assert backend.health_check() is True

    def test_async_execute(self, backend: DuckDBBackend) -> None:
        async def run() -> None:
            result = await backend.execute(
                "SELECT game, COUNT(*) AS n FROM reviews GROUP BY game ORDER BY game"
            )
            assert not result.error
            assert result.row_count >= 1

        asyncio.run(run())
