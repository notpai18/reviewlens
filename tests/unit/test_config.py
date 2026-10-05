"""Unit tests for configuration and basic setup."""

import os

from reviewlens.config import Settings, get_settings


def test_settings_load_from_env():
    """Test that settings load correctly from environment variables."""
    # Test with custom env vars
    os.environ["APP_ENV"] = "prod"
    os.environ["GEMINI_API_KEY"] = "prod_key_12345"
    os.environ["LLM_RPM_LIMIT"] = "20"

    settings = Settings()

    assert settings.app_env == "prod"
    assert settings.gemini_api_key == "prod_key_12345"
    assert settings.llm_rpm_limit == 20
    assert settings.is_production is True
    assert settings.is_development is False
    assert settings.is_test is False


def test_settings_defaults():
    """Test default settings values."""
    # Reset env to ensure defaults are tested
    os.environ["GEMINI_API_KEY"] = "default_test_key"
    os.environ["APP_ENV"] = "dev"

    settings = Settings()

    assert settings.gemini_model == "gemini-2.5-flash-lite"
    assert settings.llm_timeout_s == 45
    assert settings.duckdb_path.name == "reviewlens.duckdb"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.retrieval_mode == "hybrid_rrf"
    assert settings.retrieval_top_k == 8
    assert settings.sql_max_rows == 500
    assert settings.agent_max_llm_calls == 8
    assert settings.is_development is True


def test_settings_test_env():
    """Test test environment properties."""
    os.environ["APP_ENV"] = "test"
    settings = Settings()
    assert settings.is_test is True
    assert settings.is_production is False
    assert settings.is_development is False


def test_get_settings_caching():
    """Test that get_settings caches the result."""
    settings_1 = get_settings()
    settings_2 = get_settings()

    assert settings_1 is settings_2
