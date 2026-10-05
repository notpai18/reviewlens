"""Tests for search indexing."""

from unittest.mock import MagicMock, patch

from reviewlens.search.indexer import point_id, setup_collection, upsert_batch


def test_setup_collection():
    client = MagicMock()
    client.collection_exists.return_value = False
    setup_collection(client, "test_col")

    client.create_collection.assert_called_once()
    kwargs = client.create_collection.call_args.kwargs
    assert kwargs["collection_name"] == "test_col"
    assert "dense" in kwargs["vectors_config"]
    assert "bm25" in kwargs["sparse_vectors_config"]

    assert client.create_payload_index.call_count == 4
    calls = client.create_payload_index.call_args_list
    fields = [c.kwargs["field_name"] for c in calls]
    assert set(fields) == {"game", "app_version", "rating", "review_date"}


@patch("reviewlens.search.indexer.get_dense_model")
@patch("reviewlens.search.indexer.get_sparse_model")
def test_upsert_batch(mock_get_sparse, mock_get_dense):
    mock_dense = MagicMock()
    arr1 = MagicMock()
    arr1.tolist.return_value = [0.1]
    arr2 = MagicMock()
    arr2.tolist.return_value = [0.2]
    mock_dense.passage_embed.return_value = iter([arr1, arr2])
    mock_get_dense.return_value = mock_dense

    mock_sparse = MagicMock()
    sp1 = MagicMock()
    sp1.indices.tolist.return_value = [1]
    sp1.values.tolist.return_value = [0.5]
    sp2 = MagicMock()
    sp2.indices.tolist.return_value = [2]
    sp2.values.tolist.return_value = [0.6]
    mock_sparse.embed.return_value = iter([sp1, sp2])
    mock_get_sparse.return_value = mock_sparse

    client = MagicMock()

    records = [
        {
            "review_id": "r1",
            "game": "Game A",
            "review_date": "2024-01-01",
            "rating": 5,
            "thumbs_up": 10,
            "app_version": "1.0",
            "dev_replied": False,
            "text": "t1",
        },
        {
            "review_id": "r2",
            "game": "Game B",
            "review_date": "2024-01-02",
            "rating": 1,
            "thumbs_up": 0,
            "app_version": None,
            "dev_replied": True,
            "text": "t2",
        },
    ]

    upsert_batch(client, "test_col", records)

    mock_dense.passage_embed.assert_called_once_with(["t1", "t2"])
    mock_sparse.embed.assert_called_once_with(["t1", "t2"])

    client.upsert.assert_called_once()
    kwargs = client.upsert.call_args.kwargs
    assert kwargs["collection_name"] == "test_col"
    points = kwargs["points"]
    assert len(points) == 2

    p1 = points[0]
    assert p1.id == point_id("r1")
    assert p1.vector["dense"] == [0.1]
    assert p1.vector["bm25"].indices == [1]

    p2 = points[1]
    assert p2.id == point_id("r2")
    assert p2.payload["game"] == "Game B"
    assert p2.payload["app_version"] is None
