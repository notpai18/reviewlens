"""Catalog loader: schema.yaml and fewshots.yaml for SQL generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml

# src/reviewlens/warehouse/catalog.py -> parents: warehouse, reviewlens, src, <repo root>
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _find_catalog_dir() -> Path:
    """Find the catalog directory relative to this file or CWD."""
    # Try relative to the installed package (src/reviewlens/warehouse/catalog.py -> data/catalog)
    candidates = [
        _REPO_ROOT / "data" / "catalog",
        Path("data/catalog"),
    ]
    for c in candidates:
        if c.exists():
            return c
    # Fall back to CWD-relative
    return Path("data/catalog")


def load_schema_yaml(path: Path | None = None) -> dict[str, Any]:
    """Load data/catalog/schema.yaml and return its contents."""
    if path is None:
        path = _find_catalog_dir() / "schema.yaml"
    with open(path, encoding="utf-8") as f:
        return cast(dict[str, Any], yaml.safe_load(f))


def load_fewshots_yaml(path: Path | None = None) -> list[dict[str, Any]]:
    """Load data/catalog/fewshots.yaml and return the list of examples."""
    if path is None:
        path = _find_catalog_dir() / "fewshots.yaml"
    with open(path, encoding="utf-8") as f:
        return cast(list[dict[str, Any]], yaml.safe_load(f) or [])


def load_meta_json(path: Path | None = None) -> dict[str, Any]:
    """Load data/processed/meta.json."""
    if path is None:
        candidates = [
            _REPO_ROOT / "data" / "processed" / "meta.json",
            Path("data/processed/meta.json"),
        ]
        for c in candidates:
            if c.exists():
                path = c
                break
        else:
            path = Path("data/processed/meta.json")
    with open(path, encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def schema_text(schema: dict[str, Any]) -> str:
    """Render schema YAML as a compact text for prompt injection."""
    lines: list[str] = []
    table = schema.get("table", "reviews")
    lines.append(f"Table: {table}")
    lines.append("Columns:")
    for col in schema.get("columns", []):
        nullable = " (nullable)" if col.get("nullable") else ""
        desc = col.get("description", "")
        allowed = ""
        if col.get("allowed_values"):
            allowed = f" Allowed: {col['allowed_values']}"
        lines.append(f"  {col['name']} {col['type']}{nullable} - {desc}{allowed}")
    if schema.get("notes"):
        lines.append("Notes:")
        for note in schema["notes"]:
            lines.append(f"  - {note}")
    return "\n".join(lines)


def meta_text(meta: dict[str, Any]) -> str:
    """Render meta.json facts as a compact text for prompt injection."""
    lines: list[str] = ["Known games and data:"]
    for g in meta.get("games", []):
        game = g["game"]
        n = g.get("n", "?")
        dmin = g.get("date_min", "?")
        dmax = g.get("date_max", "?")
        lines.append(f"  Game: {game!r} | reviews: {n} | dates: {dmin} to {dmax}")
        versions = g.get("versions", [])
        if versions:
            vlist = ", ".join(
                f"{v['version']} (n={v['n']}, first_seen={v['first_seen']})" for v in versions[:8]
            )
            lines.append(f"    Versions (top by count): {vlist}")
    data_end = meta.get("data_end_date", "?")
    lines.append(f"  data_end_date: {data_end}")
    return "\n".join(lines)


def fewshots_text(examples: list[dict[str, Any]], max_examples: int = 3) -> str:
    """Render few-shot examples as compact prompt text."""
    lines: list[str] = []
    for i, ex in enumerate(examples[:max_examples], 1):
        lines.append(f"Example {i}:")
        lines.append(f"  Q: {ex['q']}")
        lines.append(f"  SQL: {ex['sql'].strip()}")
    return "\n".join(lines)
