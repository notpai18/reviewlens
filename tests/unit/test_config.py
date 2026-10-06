"""Unit tests for configuration and basic setup."""

import os

from reviewlens.config import Settings, get_settings


def test_settings_load_from_env(monkeypatch):
    """Test that settings load correctly from environment variables."""
    # Test with custom env vars
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("GEMINI_API_KEY", "prod_key_12345")
    monkeypatch.setenv("LLM_RPM_LIMIT", "20")

    settings = Settings(_env_file=None)

    assert settings.app_env == "prod"
    assert settings.gemini_api_key == "prod_key_12345"
    assert settings.llm_rpm_limit == 20
    assert settings.is_production is True
    assert settings.is_development is False
    assert settings.is_test is False


def test_settings_defaults(monkeypatch):
    """Test default settings values."""
    # Clear environment variables with monkeypatch to ensure defaults are tested
    for key in list(os.environ):
        if key.startswith("GEMINI_") or key in (
            "APP_ENV",
            "LLM_RPM_LIMIT",
            "RETRIEVAL_MODE",
            "RETRIEVAL_TOP_K",
        ):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("GEMINI_API_KEY", "default_test_key")
    monkeypatch.setenv("GEMINI_API_KEYS", "")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    monkeypatch.setenv("APP_ENV", "dev")

    settings = Settings(_env_file=None)

    assert settings.gemini_model == "gemini-2.5-flash-lite"
    assert settings.llm_timeout_s == 45
    assert settings.duckdb_path.name == "reviewlens.duckdb"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.retrieval_mode == "hybrid_rrf"
    assert settings.retrieval_top_k == 8
    assert settings.sql_max_rows == 500
    assert settings.agent_max_llm_calls == 8
    assert settings.is_development is True
    assert settings.api_keys == ["default_test_key"]

    # Multiple keys test
    settings_multi = Settings(_env_file=None, gemini_api_key="k1, k2", gemini_api_keys="k2, k3")
    assert settings_multi.api_keys == ["k1", "k2", "k3"]


def test_settings_test_env(monkeypatch):
    """Test test environment properties."""
    monkeypatch.setenv("APP_ENV", "test")
    settings = Settings(_env_file=None)
    assert settings.is_test is True
    assert settings.is_production is False
    assert settings.is_development is False


def test_get_settings_caching():
    """Test that get_settings caches the result."""
    settings_1 = get_settings()
    settings_2 = get_settings()

    assert settings_1 is settings_2
