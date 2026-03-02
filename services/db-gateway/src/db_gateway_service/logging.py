"""Structured JSON logging."""

from __future__ import annotations

from typing import TYPE_CHECKING

from service_commons.logging import (
    VALID_LOG_LEVELS,
    JSONFormatter,
    get_named_logger,
    setup_logging,
)

if TYPE_CHECKING:
    import logging

__all__ = [
    "VALID_LOG_LEVELS",
    "JSONFormatter",
    "get_logger",
    "setup_logging",
]


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    from db_gateway_service.config import get_settings

    settings = get_settings()
    return get_named_logger(settings.service.name, name)
