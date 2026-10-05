"""Tests for the FastAPI server."""

import pytest
from fastapi.testclient import TestClient

from reviewlens.agent.factory import LLMUnavailableError
from reviewlens.api.deps import get_agent_ctx, get_settings_dep
from reviewlens.api.main import app, query_cache
from reviewlens.config import Settings
from reviewlens.models import FinalAnswer, Usage


@pytest.fixture
def client():
    # Clear the query cache
    query_cache.clear()

    # Override deps
    def _override_settings():
        return Settings(ingest_api_key="test_key", rate_limit_per_min=10)

    class MockCtx:
        class MockWarehouse:
            async def execute(self, q):
                class R:
                    row_count = 1
                    error = None

                return R()

        class MockQdrant:
            def count(self, collection_name, exact=True):
                class R:
                    count = 1

                return R()

        warehouse = MockWarehouse()
        qdrant_client = MockQdrant()

        class MockSettings:
            qdrant_collection = "test_col"
            agent_timeout_s = 60

        settings = MockSettings()

    def _override_ctx():
        return MockCtx()

    app.dependency_overrides[get_settings_dep] = _override_settings
    app.dependency_overrides[get_agent_ctx] = _override_ctx

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


def test_health(client: TestClient):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_ready(client: TestClient):
    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json() == {"status": "ready"}


def test_ask_validation_error(client: TestClient):
    res = client.post("/ask", json={"question": ""})  # empty string validation
    assert res.status_code == 422
    data = res.json()
    assert "error" in data
    assert data["error"]["code"] == "validation_error"


def test_ingest_no_auth(client: TestClient):
    res = client.post("/ingest", json={"mode": "rebuild"})
    assert res.status_code == 401


def test_ingest_auth_and_status(client: TestClient):
    # Test requires X-API-Key and should succeed if parquet exists
    res = client.post("/ingest", json={"mode": "rebuild"}, headers={"X-API-Key": "test_key"})
    assert res.status_code == 200
    assert res.json() == {"status": "started"}

    status_res = client.get("/ingest/status", headers={"X-API-Key": "test_key"})
    assert status_res.status_code == 200
    assert "status" in status_res.json()


def test_body_size_limit(client: TestClient):
    res = client.post("/ask", headers={"content-length": "9000"}, json={"question": "a"})
    assert res.status_code == 413


def test_ask_success_and_cache(client: TestClient, monkeypatch):
    async def mock_run_question(ctx, question, history=None):
        return {
            "final_answer": FinalAnswer(
                summary="Test Answer", findings=[], caveats=[], followups=[]
            ),
            "sql_result": None,
            "docs_result": [],
            "trace": [],
            "usage": Usage(),
        }

    monkeypatch.setattr("reviewlens.api.main.run_question", mock_run_question)

    res1 = client.post("/ask", json={"question": "test question", "history": []})
    assert res1.status_code == 200
    assert res1.json()["answer_markdown"] == "Test Answer"
    assert res1.json()["cached"] is False

    res2 = client.post("/ask", json={"question": "test question", "history": []})
    assert res2.status_code == 200
    assert res2.json()["cached"] is True


def test_ask_upstream_llm_error(client: TestClient, monkeypatch):
    async def mock_run_question(ctx, question, history=None):
        raise LLMUnavailableError("API key missing")

    monkeypatch.setattr("reviewlens.api.main.run_question", mock_run_question)

    res = client.post("/ask", json={"question": "fail", "history": []})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "upstream_llm_error"


def test_ask_timeout(client: TestClient, monkeypatch):
    async def mock_run_question(ctx, question, history=None):
        raise TimeoutError("Simulated timeout")

    monkeypatch.setattr("reviewlens.api.main.run_question", mock_run_question)

    res = client.post("/ask", json={"question": "timeout", "history": []})
    assert res.status_code == 504
    assert res.json()["error"]["code"] == "timeout"
