"""Collection initialization and indexing for Qdrant."""

import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from reviewlens.search.hybrid import DENSE_DIM, get_dense_model, get_sparse_model


def point_id(review_id: str) -> str:
    """Deterministic Qdrant point id, so re-ingestion is idempotent (Section 6.6)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, review_id))


def create_collection(client: QdrantClient, collection: str) -> None:
    """Create the collection (named dense + sparse BM25 vectors) and payload indexes."""
    client.create_collection(
        collection_name=collection,
        vectors_config={
            "dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE),
        },
        sparse_vectors_config={
            "bm25": qm.SparseVectorParams(modifier=qm.Modifier.IDF),
        },
    )

    client.create_payload_index(
        collection_name=collection, field_name="game", field_schema=qm.PayloadSchemaType.KEYWORD
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="app_version",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=collection, field_name="rating", field_schema=qm.PayloadSchemaType.INTEGER
    )
    client.create_payload_index(
        collection_name=collection,
        field_name="review_date",
        field_schema=qm.PayloadSchemaType.DATETIME,
    )


def setup_collection(client: QdrantClient, collection: str, recreate: bool = True) -> None:
    """Ensure the collection exists. With ``recreate=True`` any existing one is dropped first."""
    exists = client.collection_exists(collection)
    if exists and not recreate:
        return
    if exists:
        client.delete_collection(collection)
    create_collection(client, collection)


def upsert_batch(client: QdrantClient, collection: str, records: list[dict[str, Any]]) -> None:
    """Embed and upsert a batch of records."""
    if not records:
        return

    dense = get_dense_model()
    sparse = get_sparse_model()

    texts = [r["text"] for r in records]

    dense_vecs = list(dense.passage_embed(texts))
    sparse_vecs = list(sparse.embed(texts))

    points = []
    for i, r in enumerate(records):
        d = dense_vecs[i].tolist()
        s = sparse_vecs[i]

        points.append(
            qm.PointStruct(
                id=point_id(str(r["review_id"])),
                vector={
                    "dense": d,
                    "bm25": qm.SparseVector(indices=s.indices.tolist(), values=s.values.tolist()),
                },
                payload={
                    "review_id": r["review_id"],
                    "game": r["game"],
                    "review_date": r["review_date"],
                    "rating": r["rating"],
                    "thumbs_up": r["thumbs_up"],
                    "app_version": r.get("app_version"),
                    "dev_replied": r["dev_replied"],
                    "text": r["text"],
                },
            )
        )

    client.upsert(collection_name=collection, points=points)
