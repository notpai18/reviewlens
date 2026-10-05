"""Tests for hybrid search implementation."""

from datetime import date
from unittest.mock import MagicMock, patch

from qdrant_client import models as qm

from reviewlens.models import Filters, RetrievalMode
from reviewlens.search.hybrid import _build_filter, search


def test_build_filter_empty():
    assert _build_filter(Filters()) is None


def test_build_filter_game():
    flt = _build_filter(Filters(game="Brawl Stars"))
    assert len(flt.must) == 1
    assert flt.must[0].key == "game"
    assert flt.must[0].match.value == "Brawl Stars"


def test_build_filter_rating_range():
    flt = _build_filter(Filters(rating_min=2, rating_max=4))
    assert len(flt.must) == 1
    assert flt.must[0].key == "rating"
    assert flt.must[0].range.gte == 2.0
    assert flt.must[0].range.lte == 4.0


def test_build_filter_date_range():
    flt = _build_filter(Filters(date_from=date(2023, 1, 1), date_to=date(2023, 12, 31)))
    assert len(flt.must) == 1
    assert flt.must[0].key == "review_date"
    assert flt.must[0].range.gte == date(2023, 1, 1)
    assert flt.must[0].range.lte == date(2023, 12, 31)


def test_build_filter_app_versions():
    flt = _build_filter(Filters(app_versions=["1.0.0", "1.0.1"]))
    assert len(flt.must) == 1
    assert flt.must[0].key == "app_version"
    assert flt.must[0].match.any == ["1.0.0", "1.0.1"]


def test_build_filter_combined():
    flt = _build_filter(
        Filters(game="Brawl Stars", rating_min=1, rating_max=3, app_versions=["1.0"])
    )
    assert len(flt.must) == 3


@patch("reviewlens.search.hybrid.get_dense_model")
@patch("reviewlens.search.hybrid.get_sparse_model")
def test_search_dense_mode(mock_get_sparse, mock_get_dense):
    mock_dense = MagicMock()
    mock_arr = MagicMock()
    mock_arr.tolist.return_value = [0.1, 0.2]
    mock_dense.query_embed.return_value = iter([mock_arr])
    mock_get_dense.return_value = mock_dense

    mock_sparse = MagicMock()
    mock_get_sparse.return_value = mock_sparse

    client = MagicMock()
    client.query_points.return_value = MagicMock(
        points=[
            qm.ScoredPoint(
                id=1,
                version=1,
                score=0.9,
                payload={
                    "review_id": "r1",
                    "game": "Game1",
                    "review_date": "2024-01-01",
                    "rating": 5,
                    "text": "great game",
                },
            )
        ]
    )

    res = search(client, "test_col", "query", RetrievalMode.DENSE, Filters(), 5)

    assert len(res) == 1
    assert res[0].review_id == "r1"
    assert res[0].score == 0.9

    client.query_points.assert_called_once()
    kwargs = client.query_points.call_args.kwargs
    assert kwargs["using"] == "dense"
    assert kwargs["query"] == [0.1, 0.2]


@patch("reviewlens.search.hybrid.get_dense_model")
@patch("reviewlens.search.hybrid.get_sparse_model")
def test_search_bm25_mode(mock_get_sparse, mock_get_dense):
    mock_dense = MagicMock()
    mock_get_dense.return_value = mock_dense

    mock_sparse = MagicMock()
    sp_mock = MagicMock()
    sp_mock.indices.tolist.return_value = [1, 2]
    sp_mock.values.tolist.return_value = [0.5, 0.6]
    mock_sparse.query_embed.return_value = iter([sp_mock])
    mock_get_sparse.return_value = mock_sparse

    client = MagicMock()
    client.query_points.return_value = MagicMock(points=[])

    search(client, "test_col", "query", RetrievalMode.BM25, Filters(), 5)

    client.query_points.assert_called_once()
    kwargs = client.query_points.call_args.kwargs
    assert kwargs["using"] == "bm25"
    assert kwargs["query"].indices == [1, 2]


@patch("reviewlens.search.hybrid.get_dense_model")
@patch("reviewlens.search.hybrid.get_sparse_model")
def test_search_hybrid_rrf_mode(mock_get_sparse, mock_get_dense):
    mock_dense = MagicMock()
    mock_arr = MagicMock()
    mock_arr.tolist.return_value = [0.1]
    mock_dense.query_embed.return_value = iter([mock_arr])
    mock_get_dense.return_value = mock_dense

    mock_sparse = MagicMock()
    sp_mock = MagicMock()
    sp_mock.indices.tolist.return_value = [1]
    sp_mock.values.tolist.return_value = [0.5]
    mock_sparse.query_embed.return_value = iter([sp_mock])
    mock_get_sparse.return_value = mock_sparse

    client = MagicMock()
    client.query_points.return_value = MagicMock(points=[])

    search(client, "test_col", "query", RetrievalMode.HYBRID_RRF, Filters(), 5)

    client.query_points.assert_called_once()
    kwargs = client.query_points.call_args.kwargs
    assert len(kwargs["prefetch"]) == 2
    assert kwargs["query"].fusion == qm.Fusion.RRF
