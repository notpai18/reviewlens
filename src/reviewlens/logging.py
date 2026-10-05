"""Structured logging configuration for ReviewLens."""

import logging
import sys
from typing import Any

import structlog
from structlog.typing import FilteringBoundLogger

from .config import get_settings


def configure_logging() -> FilteringBoundLogger:
    """Configure structured logging for the application."""
    settings = get_settings()

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if settings.is_production else logging.DEBUG,
    )

    # Configure structlog
    processors: list[Any] = [
        # Add timestamp
        structlog.processors.TimeStamper(fmt="iso"),
        # Add log level
        structlog.stdlib.add_log_level,
        # Add logger name
        structlog.stdlib.add_logger_name,
        # Process stack info
        structlog.processors.StackInfoRenderer(),
        # Format exceptions
        structlog.dev.set_exc_info,
    ]

    if settings.is_production:
        # JSON output for production
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Pretty console output for development
        processors.extend(
            [
                structlog.dev.ConsoleRenderer(colors=True),
            ]
        )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    return structlog.get_logger()  # type: ignore[no-any-return]


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def mask_sensitive_data(data: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive data in log entries."""
    sensitive_patterns = {
        "api_key",
        "apikey",
        "password",
        "secret",
        "token",
        "auth",
    }

    masked_data: dict[str, Any] = {}
    for key, value in data.items():
        k_lower = key.lower()
        if any(p in k_lower for p in sensitive_patterns) or k_lower == "key":
            masked_data[key] = "***MASKED***"
        elif isinstance(value, dict):
            masked_data[key] = mask_sensitive_data(value)
        else:
            masked_data[key] = value

    return masked_data


# Initialize logging on module import
logger = configure_logging()
