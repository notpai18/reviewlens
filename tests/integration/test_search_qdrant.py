"""Integration tests for Qdrant vector store and hybrid search.

Tests upsert, querying, idempotent re-ingestion, payload filters, BM25 exact match,
and dense semantic match. Uses Qdrant Client in-memory or running local instance.
"""

from __future__ import annotations

import pytest
from qdrant_client import QdrantClient

from reviewlens.models import Filters, RetrievalMode
from reviewlens.search.hybrid import search
from reviewlens.search.indexer import setup_collection, upsert_batch

pytestmark = pytest.mark.integration


@pytest.fixture
def memory_client() -> QdrantClient:
    """Provide an isolated in-memory Qdrant client."""
    client = QdrantClient(":memory:")
    setup_collection(client, "test_reviews", recreate=True)
    return client


@pytest.fixture
def sample_records() -> list[dict]:
    """Sample records with diverse text and metadata."""
    return [
        {
            "review_id": "rev_001",
            "game": "Game Alpha",
            "review_date": "2024-03-01",
            "rating": 5,
            "thumbs_up": 12,
            "app_version": "1.2.0",
            "dev_replied": True,
            "text": "The matchmaking system is superb and fast, loving the new deck upgrades!",
        },
        {
            "review_id": "rev_002",
            "game": "Game Alpha",
            "review_date": "2024-03-15",
            "rating": 1,
            "thumbs_up": 3,
            "app_version": "1.2.0",
            "dev_replied": False,
            "text": "Terrible lag and constant crashing after the latest patch. Unplayable freeze bug!",
        },
        {
            "review_id": "rev_003",
            "game": "Game Beta",
            "review_date": "2024-04-10",
            "rating": 3,
            "thumbs_up": 0,
            "app_version": "2.0.1",
            "dev_replied": False,
            "text": "Average card battler, graphics are pretty good but progression feels sluggish.",
        },
        {
            "review_id": "rev_004",
            "game": "Game Beta",
            "review_date": "2024-05-20",
            "rating": 5,
            "thumbs_up": 8,
            "app_version": "2.1.0",
            "dev_replied": True,
            "text": "Spectacular visuals and amazing art direction, absolutely stunning character designs!",
        },
    ]


def test_upsert_and_count(memory_client: QdrantClient, sample_records: list[dict]):
    """Test upserting batch records and counting points."""
    upsert_batch(memory_client, "test_reviews", sample_records)
    count = memory_client.count("test_reviews", exact=True).count
    assert count == 4


def test_idempotent_reingest(memory_client: QdrantClient, sample_records: list[dict]):
    """Re-ingesting the exact same records produces no duplicates."""
    upsert_batch(memory_client, "test_reviews", sample_records)
    assert memory_client.count("test_reviews", exact=True).count == 4

    # Re-ingest second time
    upsert_batch(memory_client, "test_reviews", sample_records)
    assert memory_client.count("test_reviews", exact=True).count == 4


def test_filters_restrict_results(memory_client: QdrantClient, sample_records: list[dict]):
    """Payload filters restrict results by game, rating, and app_version."""
    upsert_batch(memory_client, "test_reviews", sample_records)

    # Filter by game
    flt_game = Filters(game="Game Alpha")
    results = search(
        memory_client,
        "test_reviews",
        query="matchmaking",
        mode=RetrievalMode.BM25,
        filters=flt_game,
        k=10,
    )
    assert all(r.game == "Game Alpha" for r in results)
    assert len(results) > 0

    # Filter by rating range
    flt_rating = Filters(rating_min=1, rating_max=2)
    results_rating = search(
        memory_client,
        "test_reviews",
        query="bug crash",
        mode=RetrievalMode.BM25,
        filters=flt_rating,
        k=10,
    )
    assert all(r.rating <= 2 for r in results_rating)
    assert any(r.review_id == "rev_002" for r in results_rating)

    # Filter by version
    flt_ver = Filters(app_versions=["2.1.0"])
    results_ver = search(
        memory_client,
        "test_reviews",
        query="visuals",
        mode=RetrievalMode.BM25,
        filters=flt_ver,
        k=10,
    )
    assert len(results_ver) == 1
    assert results_ver[0].review_id == "rev_004"


def test_bm25_exact_token_match(memory_client: QdrantClient, sample_records: list[dict]):
    """BM25 successfully retrieves document containing exact distinctive token."""
    upsert_batch(memory_client, "test_reviews", sample_records)

    results = search(
        memory_client,
        "test_reviews",
        query="unplayable freeze bug",
        mode=RetrievalMode.BM25,
        filters=Filters(),
        k=3,
    )
    assert len(results) > 0
    assert results[0].review_id == "rev_002"


def test_dense_semantic_paraphrase_match(memory_client: QdrantClient, sample_records: list[dict]):
    """Dense embedding retrieves document by semantic similarity without exact keyword overlap."""
    upsert_batch(memory_client, "test_reviews", sample_records)

    # Query uses "gorgeous artwork and aesthetic style", matching "spectacular visuals and amazing art direction"
    results = search(
        memory_client,
        "test_reviews",
        query="gorgeous artwork and aesthetic style",
        mode=RetrievalMode.DENSE,
        filters=Filters(),
        k=3,
    )
    assert len(results) > 0
    assert results[0].review_id == "rev_004"


def test_hybrid_rrf_retrieval(memory_client: QdrantClient, sample_records: list[dict]):
    """Hybrid RRF combines sparse and dense signals."""
    upsert_batch(memory_client, "test_reviews", sample_records)

    results = search(
        memory_client,
        "test_reviews",
        query="fast matchmaking deck upgrades",
        mode=RetrievalMode.HYBRID_RRF,
        filters=Filters(),
        k=3,
    )
    assert len(results) > 0
    assert results[0].review_id == "rev_001"
