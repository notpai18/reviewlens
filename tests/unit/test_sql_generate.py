"""Tests for SQL generation loop."""

import json
from pathlib import Path

import pytest

from reviewlens.llm.fake import FakeLLM
from reviewlens.sql.generate import SQLGenerator
from reviewlens.warehouse.duckdb_backend import DuckDBBackend


@pytest.fixture
def fake_warehouse(tmp_path: Path) -> DuckDBBackend:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE reviews (game VARCHAR, rating INTEGER)")
    con.execute("INSERT INTO reviews VALUES ('A', 5), ('A', 1)")
    con.close()
    return DuckDBBackend(db_path=db_path)


def write_dummy_catalogs(tmp_path: Path):
    (tmp_path / "schema.yaml").write_text(
        "table: reviews\ncolumns:\n  - name: rating\n    type: INTEGER"
    )
    (tmp_path / "fewshots.yaml").write_text("- q: foo\n  sql: bar")
    (tmp_path / "meta.json").write_text(json.dumps({"games": [{"game": "A"}]}))


@pytest.mark.asyncio
async def test_sql_generate_success_first_try(fake_warehouse, tmp_path):
    write_dummy_catalogs(tmp_path)
    llm = FakeLLM(responses=['{"sql": "SELECT COUNT(*) FROM reviews", "assumptions": []}'])
    generator = SQLGenerator(
        llm=llm,
        warehouse=fake_warehouse,
        schema_path=tmp_path / "schema.yaml",
        fewshots_path=tmp_path / "fewshots.yaml",
        meta_path=tmp_path / "meta.json",
    )
    generator._schema_text = ""
    generator._meta_text = ""
    generator._fewshots_text = ""

    result = await generator.run("How many reviews?")
    assert result.success
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "success"
    assert result.final_sql == "SELECT COUNT(*) FROM reviews LIMIT 500"


@pytest.mark.asyncio
async def test_sql_generate_repair_validation_error(fake_warehouse, tmp_path):
    write_dummy_catalogs(tmp_path)
    llm = FakeLLM(
        responses=[
            '{"sql": "DROP TABLE reviews", "assumptions": []}',
            '{"sql": "SELECT COUNT(*) FROM reviews", "assumptions": []}',
        ]
    )
    generator = SQLGenerator(
        llm=llm,
        warehouse=fake_warehouse,
        schema_path=tmp_path / "schema.yaml",
        fewshots_path=tmp_path / "fewshots.yaml",
        meta_path=tmp_path / "meta.json",
    )
    generator._schema_text = ""
    generator._meta_text = ""
    generator._fewshots_text = ""

    result = await generator.run("How many reviews?")
    assert result.success
    assert len(result.attempts) == 2
    assert result.attempts[0].status == "validation_error"
    assert result.attempts[1].status == "success"
    assert result.final_sql == "SELECT COUNT(*) FROM reviews LIMIT 500"


@pytest.mark.asyncio
async def test_sql_generate_repair_db_error(fake_warehouse, tmp_path):
    write_dummy_catalogs(tmp_path)
    llm = FakeLLM(
        responses=[
            '{"sql": "SELECT nonexistent FROM reviews", "assumptions": []}',
            '{"sql": "SELECT rating FROM reviews", "assumptions": []}',
        ]
    )
    generator = SQLGenerator(
        llm=llm,
        warehouse=fake_warehouse,
        schema_path=tmp_path / "schema.yaml",
        fewshots_path=tmp_path / "fewshots.yaml",
        meta_path=tmp_path / "meta.json",
    )
    generator._schema_text = ""
    generator._meta_text = ""
    generator._fewshots_text = ""

    result = await generator.run("Get ratings")
    assert result.success
    assert len(result.attempts) == 2
    assert result.attempts[0].status == "db_error"
    assert result.attempts[1].status == "success"
    assert result.final_sql == "SELECT rating FROM reviews LIMIT 500"


@pytest.mark.asyncio
async def test_sql_generate_max_attempts_exceeded(fake_warehouse, tmp_path):
    write_dummy_catalogs(tmp_path)
    llm = FakeLLM(
        responses=[
            '{"sql": "DROP TABLE reviews", "assumptions": []}',
            '{"sql": "DROP TABLE reviews", "assumptions": []}',
            '{"sql": "DROP TABLE reviews", "assumptions": []}',
            '{"sql": "DROP TABLE reviews", "assumptions": []}',
        ]
    )
    generator = SQLGenerator(
        llm=llm,
        warehouse=fake_warehouse,
        schema_path=tmp_path / "schema.yaml",
        fewshots_path=tmp_path / "fewshots.yaml",
        meta_path=tmp_path / "meta.json",
        max_attempts=3,
    )
    generator._schema_text = ""
    generator._meta_text = ""
    generator._fewshots_text = ""

    result = await generator.run("Break it")
    assert not result.success
    assert len(result.attempts) == 3
    for a in result.attempts:
        assert a.status == "validation_error"


# ---------------------------------------------------------------------------
# Empty-result repair, LLM failures, CLI
# ---------------------------------------------------------------------------


def _gen(fake_warehouse, tmp_path, llm, **kw):
    write_dummy_catalogs(tmp_path)
    return SQLGenerator(
        llm=llm,
        warehouse=fake_warehouse,
        schema_path=tmp_path / "schema.yaml",
        fewshots_path=tmp_path / "fewshots.yaml",
        meta_path=tmp_path / "meta.json",
        **kw,
    )


EMPTY_SQL = '{"sql": "SELECT * FROM reviews WHERE game = \'Nope\'", "assumptions": []}'
GOOD_SQL = '{"sql": "SELECT COUNT(*) FROM reviews", "assumptions": []}'


@pytest.mark.asyncio
async def test_empty_result_triggers_exactly_one_repair(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=[EMPTY_SQL, GOOD_SQL])
    result = await _gen(fake_warehouse, tmp_path, llm).run("q")
    assert result.success
    assert [a.status for a in result.attempts] == ["empty", "success"]
    assert llm.call_count == 2
    # repair prompt carried the empty-result feedback
    assert "0 rows" in llm.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_second_empty_result_is_accepted_not_repaired_again(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=[EMPTY_SQL, EMPTY_SQL, GOOD_SQL])
    result = await _gen(fake_warehouse, tmp_path, llm).run("q")
    assert result.success
    assert result.result is not None and result.result.row_count == 0
    assert [a.status for a in result.attempts] == ["empty", "success"]
    assert llm.call_count == 2  # third scripted response never consumed


@pytest.mark.asyncio
async def test_empty_result_with_single_attempt_budget_is_success(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=[EMPTY_SQL])
    result = await _gen(fake_warehouse, tmp_path, llm, max_attempts=1).run("q")
    assert result.success
    assert result.result is not None and result.result.row_count == 0
    assert [a.status for a in result.attempts] == ["success"]


@pytest.mark.asyncio
async def test_empty_result_after_validation_error_still_gets_its_repair(fake_warehouse, tmp_path):
    llm = FakeLLM(
        responses=['{"sql": "DROP TABLE reviews", "assumptions": []}', EMPTY_SQL, GOOD_SQL]
    )
    result = await _gen(fake_warehouse, tmp_path, llm, max_attempts=3).run("q")
    assert result.success
    assert [a.status for a in result.attempts] == ["validation_error", "empty", "success"]


@pytest.mark.asyncio
async def test_llm_error_on_generate_is_reported(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=[])  # unexpected call -> FakeLLM raises
    result = await _gen(fake_warehouse, tmp_path, llm).run("q")
    assert not result.success
    assert result.attempts == []
    assert "LLM generate error" in (result.error_summary or "")


@pytest.mark.asyncio
async def test_llm_error_on_repair_is_reported(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=['{"sql": "DROP TABLE reviews", "assumptions": []}'])
    result = await _gen(fake_warehouse, tmp_path, llm).run("q")
    assert not result.success
    assert [a.status for a in result.attempts] == ["validation_error"]
    assert "LLM repair error" in (result.error_summary or "")


def test_run_sync_wrapper(fake_warehouse, tmp_path):
    llm = FakeLLM(responses=[GOOD_SQL])
    result = _gen(fake_warehouse, tmp_path, llm).run_sync("q")
    assert result.success


@pytest.mark.asyncio
async def test_cli_main_prints_success_and_failure(fake_warehouse, tmp_path, monkeypatch, capsys):
    import reviewlens.sql.generate as g

    ok_gen = _gen(fake_warehouse, tmp_path, FakeLLM(responses=[GOOD_SQL]))
    monkeypatch.setattr(g, "_build_generator_from_settings", lambda: ok_gen)
    await g._main("how many?")
    out = capsys.readouterr().out
    assert "Success: True" in out and "Rows (1)" in out and "SELECT COUNT(*)" in out

    bad_gen = _gen(fake_warehouse, tmp_path, FakeLLM(responses=[]))
    monkeypatch.setattr(g, "_build_generator_from_settings", lambda: bad_gen)
    await g._main("how many?")
    assert "Error: LLM generate error" in capsys.readouterr().out


def test_build_generator_from_settings(monkeypatch, tmp_path):
    import reviewlens.sql.generate as g
    from reviewlens.config import get_settings

    write_dummy_catalogs(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-not-used")
    monkeypatch.setenv("DATA_META_PATH", str(tmp_path / "meta.json"))
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "x.duckdb"))
    get_settings.cache_clear()
    try:
        gen = g._build_generator_from_settings()
    finally:
        get_settings.cache_clear()
    assert isinstance(gen, SQLGenerator)


@pytest.mark.asyncio
async def test_sql_generate_guard_triggers_repair_for_small_sample_ranking(
    fake_warehouse, tmp_path
):
    write_dummy_catalogs(tmp_path)
    # Attempt 1 emits query without HAVING -> triggers guard
    # Attempt 2 (repair) emits query with HAVING COUNT(*) >= 2 -> succeeds
    llm = FakeLLM(
        responses=[
            '{"sql": "SELECT game, AVG(rating) as avg_r FROM reviews GROUP BY game ORDER BY avg_r ASC", "assumptions": []}',
            '{"sql": "SELECT game, AVG(rating) as avg_r FROM reviews GROUP BY game HAVING COUNT(*) >= 2 ORDER BY avg_r ASC", "assumptions": [], "what_changed": "added HAVING"}',
        ]
    )
    generator = _gen(fake_warehouse, tmp_path, llm)
    result = await generator.run("What is the lowest rated game?")
    assert result.success
    assert len(result.attempts) == 2
    assert result.attempts[0].status == "small_sample_guard"
    assert "HAVING" in result.attempts[0].error
    assert result.attempts[1].status == "success"


@pytest.mark.asyncio
async def test_sql_generate_guard_allows_order_by_count(fake_warehouse, tmp_path):
    write_dummy_catalogs(tmp_path)
    # Query ordering by COUNT(*) must not trigger guard
    llm = FakeLLM(
        responses=[
            '{"sql": "SELECT game, COUNT(*) as c FROM reviews GROUP BY game ORDER BY c DESC", "assumptions": []}'
        ]
    )
    generator = _gen(fake_warehouse, tmp_path, llm)
    result = await generator.run("Which game has the most reviews?")
    assert result.success
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "success"
