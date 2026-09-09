"""
Structured logging configuration with password-safe filtering.

Uses structlog for JSON-formatted, context-rich logging.
Sensitive fields (password, token, secret, credential) are automatically redacted.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Fields that must never appear in logs
_SENSITIVE_PATTERNS = re.compile(
    r"(password|passwd|secret|token|credential|api_key|private_key)",
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"


def _redact_sensitive(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that redacts sensitive values."""
    for key in list(event_dict.keys()):
        if _SENSITIVE_PATTERNS.search(key):
            event_dict[key] = _REDACTED
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured logging for the application."""

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper(), logging.INFO),
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            _redact_sensitive,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)
