"""Test suite configuration and shared fixtures."""

import os
from unittest.mock import MagicMock

import pytest

# Set test environment
os.environ["APP_ENV"] = "test"
os.environ["GEMINI_API_KEY"] = "test_key_for_testing"


@pytest.fixture
def mock_gemini_client():
    """Mock Gemini client for testing."""
    client = MagicMock()
    return client


@pytest.fixture
def test_settings():
    """Test settings instance."""
    from reviewlens.config import Settings

    return Settings(
        app_env="test",
        gemini_api_key="test_key_12345",
        gemini_model="gemini-2.0-flash-exp",
        llm_rpm_limit=10,
        llm_timeout_s=5,
    )
