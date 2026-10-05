"""Unit tests for logging module."""

import os

from reviewlens.logging import configure_logging, get_logger, mask_sensitive_data


def test_mask_sensitive_data():
    """Test sensitive data masking in logs."""
    data = {
        "gemini_api_key": "secret_123",
        "nested": {
            "password": "pass",
            "safe": "hello",
        },
        "normal_key": "safe_val",
    }
    masked = mask_sensitive_data(data)
    assert masked["gemini_api_key"] == "***MASKED***"
    assert masked["nested"]["password"] == "***MASKED***"
    assert masked["nested"]["safe"] == "hello"
    assert masked["normal_key"] == "safe_val"


def test_get_logger():
    """Test get_logger returns a bound logger."""
    log = get_logger("test_module")
    assert log is not None


def test_configure_logging_prod():
    """Test logging configuration for production."""
    os.environ["APP_ENV"] = "prod"
    from reviewlens import config

    config.get_settings.cache_clear()

    log = configure_logging()
    assert log is not None

    os.environ["APP_ENV"] = "test"
    config.get_settings.cache_clear()
