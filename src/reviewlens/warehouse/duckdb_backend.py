"""DuckDB warehouse executor (Section 7.1).

Opens DuckDB read-only with external access disabled. Each query runs in
asyncio.to_thread with a timeout via threading.Timer + con.interrupt().

Library version: duckdb 1.5.6
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from reviewlens.models import SQLResult


def _json_safe(value: Any) -> Any:
    """Convert a value to a JSON-safe type."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _make_rows_safe(rows: list[tuple[Any, ...]]) -> list[list[Any]]:
    return [[_json_safe(v) for v in row] for row in rows]


def _execute_sync(
    db_path: str,
    sql: str,
    max_rows: int,
    timeout_s: int,
) -> SQLResult:
    """Synchronous execution, designed to be called from asyncio.to_thread."""
    # Use a per-call connection so read_only enforcement is per-call safe.
    con = duckdb.connect(db_path, read_only=True)
    try:
        # Disable external access for extra safety (works after read_only connect)
        try:
            con.execute("SET enable_external_access=false")
        except Exception:
            pass  # If this setting isn't available, the read_only flag still protects us
        con.execute("SET memory_limit='512MB'")

        interrupted = threading.Event()

        def _interrupt() -> None:
            interrupted.set()
            try:
                con.interrupt()
            except Exception:
                pass

        timer = threading.Timer(float(timeout_s), _interrupt)
        timer.start()
        t0 = time.monotonic()
        try:
            cursor = con.execute(sql)
            columns = [desc[0] for desc in cursor.description or []]
            rows_raw = cursor.fetchmany(max_rows + 1)
        except duckdb.InterruptException:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return SQLResult(
                sql=sql,
                columns=[],
                rows=[],
                row_count=0,
                truncated=False,
                elapsed_ms=elapsed_ms,
                error=f"timeout: query exceeded {timeout_s}s",
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            return SQLResult(
                sql=sql,
                columns=[],
                rows=[],
                row_count=0,
                truncated=False,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
        finally:
            timer.cancel()

        elapsed_ms = int((time.monotonic() - t0) * 1000)

        truncated = len(rows_raw) > max_rows
        rows_trimmed = rows_raw[:max_rows]
        safe_rows = _make_rows_safe(list(rows_trimmed))

        return SQLResult(
            sql=sql,
            columns=columns,
            rows=safe_rows,
            row_count=len(safe_rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )
    finally:
        con.close()


class DuckDBBackend:
    """Async DuckDB executor wrapping a read-only warehouse file."""

    def __init__(self, db_path: Path, max_rows: int = 500, timeout_s: int = 15) -> None:
        self.db_path = str(db_path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    async def execute(self, sql: str) -> SQLResult:
        """Execute a validated SQL query asynchronously."""
        return await asyncio.to_thread(
            _execute_sync,
            self.db_path,
            sql,
            self.max_rows,
            self.timeout_s,
        )

    def execute_sync(self, sql: str) -> SQLResult:
        """Synchronous wrapper for tests that don't run an event loop."""
        return _execute_sync(self.db_path, sql, self.max_rows, self.timeout_s)

    def health_check(self) -> bool:
        """Return True if the DB is accessible and has a reviews table."""
        try:
            con = duckdb.connect(self.db_path, read_only=True)
            con.execute("SELECT 1 FROM reviews LIMIT 1").fetchone()
            con.close()
            return True
        except Exception:
            return False
