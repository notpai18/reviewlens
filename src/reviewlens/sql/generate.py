"""SQL generation and repair loop (Section 7.3).

Implements: generate → validate → execute → repair (up to SQL_MAX_ATTEMPTS).
Records every attempt for trace and evaluation.

CLI entry: python -m reviewlens.sql.generate "<question>"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from reviewlens.models import SQLResult, Usage, ValidationResult
from reviewlens.prompts.renderer import load_and_render
from reviewlens.sql.validator import validate_sql
from reviewlens.warehouse.catalog import (
    fewshots_text,
    load_fewshots_yaml,
    load_meta_json,
    load_schema_yaml,
    meta_text,
    schema_text,
)
from reviewlens.warehouse.duckdb_backend import DuckDBBackend

EMPTY_RESULT_FEEDBACK = (
    "The query ran but returned 0 rows. Check game names, date ranges, "
    "rating values, and version strings against the known data."
)


# ---------------------------------------------------------------------------
# Pydantic models for LLM structured outputs
# ---------------------------------------------------------------------------


class SQLGenerateResponse(BaseModel):
    sql: str
    assumptions: list[str] = []


class SQLRepairResponse(BaseModel):
    sql: str
    assumptions: list[str] = []
    what_changed: str = ""


# ---------------------------------------------------------------------------
# Attempt tracking
# ---------------------------------------------------------------------------


@dataclass
class SQLAttempt:
    attempt_num: int
    sql: str
    status: str  # "validation_error" | "db_error" | "empty" | "success"
    validation_reasons: list[str] = field(default_factory=list)
    error: str | None = None
    row_count: int = 0


@dataclass
class SQLToolResult:
    success: bool
    result: SQLResult | None
    attempts: list[SQLAttempt]
    final_sql: str | None = None
    error_summary: str | None = None
    usage: Usage = field(default_factory=Usage)


def _add_usage(acc: Usage, result: Any) -> None:
    """Accumulate one LLM result's token/latency numbers into ``acc``."""
    acc.llm_calls += 1
    acc.prompt_tokens += result.prompt_tokens
    acc.completion_tokens += result.completion_tokens
    acc.latency_ms += result.latency_ms


# ---------------------------------------------------------------------------
# SQL generator
# ---------------------------------------------------------------------------


class SQLGenerator:
    """Generates and repairs SQL using an LLM, validates with the AST validator,
    and executes on DuckDB."""

    def __init__(
        self,
        llm: Any,  # LLMClient protocol
        warehouse: DuckDBBackend,
        schema_path: Path | None = None,
        fewshots_path: Path | None = None,
        meta_path: Path | None = None,
        max_attempts: int = 3,
        max_rows: int = 500,
    ) -> None:
        self._llm = llm
        self._warehouse = warehouse
        self._max_attempts = max_attempts
        self._max_rows = max_rows

        # Load catalog data once
        self._schema = load_schema_yaml(schema_path)
        self._fewshots = load_fewshots_yaml(fewshots_path)
        self._meta = load_meta_json(meta_path)

        self._schema_text = schema_text(self._schema)
        self._meta_text = meta_text(self._meta)
        self._fewshots_text = fewshots_text(self._fewshots, max_examples=3)

    def _generate_system(self) -> str:
        return "You are a precise SQL writer for DuckDB. Return only valid JSON."

    async def _call_generate(self, question: str, usage: Usage | None = None) -> str:
        """Call LLM to generate initial SQL. Returns raw SQL string."""
        prompt = load_and_render(
            "sql_generate.md",
            schema_text=self._schema_text,
            meta_text=self._meta_text,
            fewshot_text=self._fewshots_text,
            question=question,
        )
        result = await self._llm.generate_structured(
            system=self._generate_system(),
            prompt=prompt,
            schema=SQLGenerateResponse,
            temperature=0.0,
        )
        if usage is not None:
            _add_usage(usage, result)
        data = SQLGenerateResponse.model_validate(result.data)
        return data.sql

    async def _call_repair(
        self, question: str, previous_sql: str, feedback: str, usage: Usage | None = None
    ) -> str:
        """Call LLM to repair SQL. Returns new SQL string."""
        prompt = load_and_render(
            "sql_repair.md",
            question=question,
            previous_sql=previous_sql,
            feedback=feedback,
            schema_text=self._schema_text,
            meta_text=self._meta_text,
        )
        result = await self._llm.generate_structured(
            system=self._generate_system(),
            prompt=prompt,
            schema=SQLRepairResponse,
            temperature=0.0,
        )
        if usage is not None:
            _add_usage(usage, result)
        data = SQLRepairResponse.model_validate(result.data)
        return data.sql

    async def run(self, question: str) -> SQLToolResult:
        """Generate, validate, execute, and repair SQL for the given question.

        Returns a SQLToolResult containing all attempts and the final result.
        """
        attempts: list[SQLAttempt] = []
        current_sql: str | None = None
        feedback: str | None = None
        empty_repair_used = False
        usage = Usage()

        for attempt_num in range(1, self._max_attempts + 1):
            # Generate or repair
            if attempt_num == 1:
                try:
                    current_sql = await self._call_generate(question, usage)
                except Exception as exc:
                    return SQLToolResult(
                        success=False,
                        result=None,
                        attempts=attempts,
                        error_summary=f"LLM generate error: {exc}",
                        usage=usage,
                    )
            else:
                assert feedback is not None
                assert current_sql is not None
                try:
                    current_sql = await self._call_repair(question, current_sql, feedback, usage)
                except Exception as exc:
                    return SQLToolResult(
                        success=False,
                        result=None,
                        attempts=attempts,
                        error_summary=f"LLM repair error: {exc}",
                        usage=usage,
                    )

            # Validate
            validation: ValidationResult = validate_sql(current_sql, max_limit=self._max_rows)
            normalized = validation.normalized_sql

            if not validation.ok or normalized is None:
                reason_str = "; ".join(validation.reasons) or "no normalized SQL produced"
                attempts.append(
                    SQLAttempt(
                        attempt_num=attempt_num,
                        sql=current_sql,
                        status="validation_error",
                        validation_reasons=validation.reasons,
                        error=reason_str,
                    )
                )
                feedback = f"Validation error: {reason_str}"
                continue

            # Execute (only normalized SQL)
            exec_result: SQLResult = await self._warehouse.execute(normalized)
            current_sql = normalized

            if exec_result.error:
                attempts.append(
                    SQLAttempt(
                        attempt_num=attempt_num,
                        sql=current_sql,
                        status="db_error",
                        error=exec_result.error,
                    )
                )
                feedback = f"Database error: {exec_result.error}"
                continue

            # Empty result: spend at most one repair on it, and only if an attempt remains.
            # Otherwise an empty result is a legitimate answer ("no such reviews").
            if (
                exec_result.row_count == 0
                and not empty_repair_used
                and attempt_num < self._max_attempts
            ):
                empty_repair_used = True
                attempts.append(
                    SQLAttempt(
                        attempt_num=attempt_num,
                        sql=current_sql,
                        status="empty",
                        row_count=0,
                    )
                )
                feedback = EMPTY_RESULT_FEEDBACK
                continue

            # Success
            attempts.append(
                SQLAttempt(
                    attempt_num=attempt_num,
                    sql=current_sql,
                    status="success",
                    row_count=exec_result.row_count,
                )
            )
            return SQLToolResult(
                success=True,
                result=exec_result,
                attempts=attempts,
                final_sql=current_sql,
                usage=usage,
            )

        # Exhausted attempts
        last_attempt = attempts[-1] if attempts else None
        return SQLToolResult(
            success=False,
            result=None,
            attempts=attempts,
            error_summary=f"Failed after {self._max_attempts} attempts. "
            f"Last status: {last_attempt.status if last_attempt else 'unknown'}",
            usage=usage,
        )

    def run_sync(self, question: str) -> SQLToolResult:
        """Synchronous wrapper for CLI use."""
        return asyncio.run(self.run(question))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_generator_from_settings() -> SQLGenerator:
    """Build an SQLGenerator from environment settings for CLI use."""
    from reviewlens.config import get_settings

    settings = get_settings()
    llm: Any
    if not settings.gemini_api_key:
        print("NOTE: GEMINI_API_KEY is not set (human task H2). Using FakeLLM demo generator.")
        from reviewlens.llm.fake import FakeLLM

        llm = FakeLLM(
            default_response='{"sql": "SELECT game, COUNT(*) AS n FROM reviews GROUP BY game", "assumptions": []}'
        )
    else:
        from reviewlens.llm.gemini import GeminiLLM

        llm = GeminiLLM(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            rpm_limit=settings.llm_rpm_limit,
            timeout_s=settings.llm_timeout_s,
        )
    warehouse = DuckDBBackend(
        db_path=settings.duckdb_path,
        max_rows=settings.sql_max_rows,
        timeout_s=settings.sql_timeout_s,
    )
    return SQLGenerator(
        llm=llm,
        warehouse=warehouse,
        meta_path=settings.data_meta_path,
        max_attempts=settings.sql_max_attempts,
        max_rows=settings.sql_max_rows,
    )


async def _main(question: str) -> None:
    gen = _build_generator_from_settings()
    print(f"\nQuestion: {question}\n")
    tool_result = await gen.run(question)

    print(f"Success: {tool_result.success}")
    print(f"Attempts: {len(tool_result.attempts)}")
    for a in tool_result.attempts:
        print(f"  Attempt {a.attempt_num}: {a.status}", end="")
        if a.error:
            print(f" ({a.error[:80]})", end="")
        print()

    if tool_result.success and tool_result.result:
        r = tool_result.result
        print(f"\nSQL:\n{tool_result.final_sql}")
        print(f"\nColumns: {r.columns}")
        print(f"Rows ({r.row_count}):")
        for row in r.rows[:10]:
            print(f"  {row}")
        if r.truncated:
            print("  ... (truncated)")
    else:
        print(f"\nError: {tool_result.error_summary}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m reviewlens.sql.generate '<question>'")
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    asyncio.run(_main(question))
