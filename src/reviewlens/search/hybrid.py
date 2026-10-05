"""Hybrid search module using Qdrant and fastembed."""

import threading
from datetime import date

from fastembed import SparseTextEmbedding, TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client import models as qm

from reviewlens.config import get_settings
from reviewlens.models import Filters, RetrievalMode, RetrievedReview

_dense_model: TextEmbedding | None = None
_sparse_model: SparseTextEmbedding | None = None
_model_lock = threading.Lock()

DENSE_MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384-d, cosine
SPARSE_MODEL_NAME = "Qdrant/bm25"
DENSE_DIM = 384


def get_dense_model() -> TextEmbedding:
    """Lazy initialization of the dense embedding model."""
    global _dense_model
    if _dense_model is None:
        with _model_lock:
            if _dense_model is None:
                settings = get_settings()
                _dense_model = TextEmbedding(
                    model_name=DENSE_MODEL_NAME, cache_dir=str(settings.fastembed_cache_path)
                )
    return _dense_model


def get_sparse_model() -> SparseTextEmbedding:
    """Lazy initialization of the sparse embedding model."""
    global _sparse_model
    if _sparse_model is None:
        with _model_lock:
            if _sparse_model is None:
                settings = get_settings()
                _sparse_model = SparseTextEmbedding(
                    SPARSE_MODEL_NAME, cache_dir=str(settings.fastembed_cache_path)
                )
    return _sparse_model


def _build_filter(filters: Filters) -> qm.Filter | None:
    """Build Qdrant filter from Filters model."""
    conditions: list[qm.Condition] = []
    if filters.game:
        conditions.append(qm.FieldCondition(key="game", match=qm.MatchValue(value=filters.game)))
    if filters.rating_min is not None or filters.rating_max is not None:
        conditions.append(
            qm.FieldCondition(
                key="rating",
                range=qm.Range(
                    gte=float(filters.rating_min) if filters.rating_min is not None else None,
                    lte=float(filters.rating_max) if filters.rating_max is not None else None,
                ),
            )
        )
    if filters.date_from or filters.date_to:
        conditions.append(
            qm.FieldCondition(
                key="review_date",
                range=qm.DatetimeRange(
                    gte=filters.date_from if filters.date_from else None,
                    lte=filters.date_to if filters.date_to else None,
                ),
            )
        )
    if filters.app_versions:
        conditions.append(
            qm.FieldCondition(key="app_version", match=qm.MatchAny(any=filters.app_versions))
        )

    if not conditions:
        return None
    return qm.Filter(must=conditions)


def search(
    client: QdrantClient,
    collection: str,
    query: str,
    mode: str,
    filters: Filters,
    k: int,
) -> list[RetrievedReview]:
    """Search Qdrant using the specified mode and filters."""
    dense = get_dense_model()
    sparse = get_sparse_model()

    flt = _build_filter(filters)
    settings = get_settings()
    prefetch_k = settings.retrieval_prefetch_k

    if mode == RetrievalMode.DENSE:
        dense_vec = list(dense.query_embed(query))[0].tolist()
        results = client.query_points(
            collection_name=collection,
            using="dense",
            query=dense_vec,
            query_filter=flt,
            limit=k,
            with_payload=True,
        )
    elif mode == RetrievalMode.BM25:
        sp = list(sparse.query_embed(query))[0]
        svec = qm.SparseVector(indices=sp.indices.tolist(), values=sp.values.tolist())
        results = client.query_points(
            collection_name=collection,
            using="bm25",
            query=svec,
            query_filter=flt,
            limit=k,
            with_payload=True,
        )
    elif mode == RetrievalMode.HYBRID_RRF:
        dense_vec = list(dense.query_embed(query))[0].tolist()
        sp = list(sparse.query_embed(query))[0]
        svec = qm.SparseVector(indices=sp.indices.tolist(), values=sp.values.tolist())

        results = client.query_points(
            collection_name=collection,
            prefetch=[
                qm.Prefetch(query=dense_vec, using="dense", filter=flt, limit=prefetch_k),
                qm.Prefetch(query=svec, using="bm25", filter=flt, limit=prefetch_k),
            ],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=k,
            with_payload=True,
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    ret = []
    for rank, p in enumerate(results.points, start=1):
        payload = p.payload or {}
        ret.append(
            RetrievedReview(
                review_id=str(payload["review_id"]),
                game=str(payload["game"]),
                review_date=date.fromisoformat(payload["review_date"]),
                rating=int(payload["rating"]),
                app_version=payload.get("app_version"),
                text=str(payload["text"]),
                score=float(p.score),
                rank=rank,
            )
        )
    return ret
