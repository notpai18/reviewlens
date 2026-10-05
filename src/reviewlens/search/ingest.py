"""Ingestion: load cleaned reviews from parquet and (re)build the Qdrant index.

Only reviews with ``content_words >= MIN_INDEX_WORDS`` are indexed (Section 6.6); SQL
still sees every row. Point ids are deterministic, so re-running is idempotent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from qdrant_client import QdrantClient

from reviewlens.search.indexer import setup_collection, upsert_batch

MIN_INDEX_WORDS = 8
BATCH_SIZE = 64


def _iso(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def load_records(parquet_path: Path, min_words: int = MIN_INDEX_WORDS) -> list[dict[str, Any]]:
    """Read the processed parquet and return indexable review records."""
    df = pd.read_parquet(parquet_path)
    df = df[df["content_words"] >= min_words]
    records: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        version = row.app_version
        records.append(
            {
                "review_id": str(row.review_id),
                "game": str(row.game),
                "review_date": _iso(row.review_date),
                "rating": int(row.rating),
                "thumbs_up": int(row.thumbs_up),
                "app_version": None
                if pd.isna(version) or not str(version).strip()
                else str(version),
                "dev_replied": bool(row.dev_replied),
                "text": str(row.content),
            }
        )
    return records


def _batches(items: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def rebuild_index(
    client: QdrantClient,
    collection: str,
    parquet_path: Path,
    *,
    recreate: bool = True,
    batch_size: int = BATCH_SIZE,
    on_progress: Callable[[int, int], None] | None = None,
) -> int:
    """(Re)index all eligible reviews. Returns the number of points now in the collection."""
    records = load_records(parquet_path)
    setup_collection(client, collection, recreate=recreate)
    done = 0
    for batch in _batches(records, batch_size):
        upsert_batch(client, collection, batch)
        done += len(batch)
        if on_progress:
            on_progress(done, len(records))
    return int(client.count(collection_name=collection, exact=True).count)
