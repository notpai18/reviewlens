"""Tests for catalog loaders and prompt-context rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from reviewlens.warehouse import catalog


def test_load_schema_fewshots_meta_from_explicit_paths(tmp_path: Path) -> None:
    (tmp_path / "s.yaml").write_text(yaml.safe_dump({"table": "reviews", "columns": []}))
    (tmp_path / "f.yaml").write_text(yaml.safe_dump([{"q": "Q?", "sql": "SELECT 1"}]))
    (tmp_path / "m.json").write_text(json.dumps({"games": []}))
    assert catalog.load_schema_yaml(tmp_path / "s.yaml")["table"] == "reviews"
    assert catalog.load_fewshots_yaml(tmp_path / "f.yaml")[0]["q"] == "Q?"
    assert catalog.load_meta_json(tmp_path / "m.json") == {"games": []}


def test_empty_fewshots_file_gives_empty_list(tmp_path: Path) -> None:
    (tmp_path / "f.yaml").write_text("")
    assert catalog.load_fewshots_yaml(tmp_path / "f.yaml") == []


def test_default_paths_resolve_from_repo_root() -> None:
    # run from repo root: real catalog files must be loadable with no arguments
    schema = catalog.load_schema_yaml()
    assert schema["table"] == "reviews"
    assert isinstance(catalog.load_fewshots_yaml(), list)


def test_schema_text_renders_columns_notes_and_allowed_values() -> None:
    text = catalog.schema_text(
        {
            "table": "reviews",
            "columns": [
                {
                    "name": "rating",
                    "type": "INTEGER",
                    "description": "stars",
                    "allowed_values": [1, 2],
                },
                {"name": "app_version", "type": "VARCHAR", "nullable": True, "description": "ver"},
            ],
            "notes": ["Use ILIKE for text"],
        }
    )
    assert "Table: reviews" in text
    assert "rating INTEGER - stars Allowed: [1, 2]" in text
    assert "app_version VARCHAR (nullable)" in text
    assert "- Use ILIKE for text" in text


def test_schema_text_minimal() -> None:
    assert catalog.schema_text({}).splitlines()[:2] == ["Table: reviews", "Columns:"]


def test_meta_text_includes_games_versions_and_end_date() -> None:
    text = catalog.meta_text(
        {
            "games": [
                {
                    "game": "Brawl Stars",
                    "n": 10,
                    "date_min": "2024-01-01",
                    "date_max": "2024-02-01",
                    "versions": [{"version": "1.0", "n": 3, "first_seen": "2024-01-02"}],
                },
                {"game": "NoVersions"},
            ],
            "data_end_date": "2024-02-01",
        }
    )
    assert "'Brawl Stars'" in text and "reviews: 10" in text
    assert "1.0 (n=3, first_seen=2024-01-02)" in text
    assert "'NoVersions' | reviews: ?" in text
    assert "data_end_date: 2024-02-01" in text


def test_fewshots_text_respects_max_examples() -> None:
    ex = [{"q": f"q{i}", "sql": f" SELECT {i} "} for i in range(5)]
    text = catalog.fewshots_text(ex, max_examples=3)
    assert "Example 3:" in text and "Example 4:" not in text
    assert "SQL: SELECT 0" in text  # stripped


def test_meta_falls_back_to_relative_path_when_package_copy_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No data/processed/meta.json under CWD and the repo-root copy hidden -> clear error.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catalog.Path, "exists", lambda self: False)
    with pytest.raises(FileNotFoundError, match="meta.json"):
        catalog.load_meta_json()
