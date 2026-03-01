"""
Shared structured JSON logging utilities.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    Custom formatter that outputs logs as JSON.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%d %H:%M")

        log_data: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        standard_attrs = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "exc_info",
            "exc_text",
            "thread",
            "threadName",
            "taskName",
            "message",
        }

        extra = {key: value for key, value in record.__dict__.items() if key not in standard_attrs}

        if extra:
            log_data["extra"] = extra

        return json.dumps(log_data, default=str)


VALID_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class DailyRotatingFileHandler(TimedRotatingFileHandler):
    """Rotating file handler that names files as YYYY-MM-DD.log."""

    def __init__(self, directory: str) -> None:
        self._log_directory = directory
        filename = self._make_filename()
        super().__init__(filename, when="midnight", utc=True)

    def _make_filename(self) -> str:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        return os.path.join(self._log_directory, f"{today}.log")

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        self.baseFilename = os.path.abspath(self._make_filename())
        if not self.delay:
            self.stream = self._open()
        current_time = int(time.time())
        self.rolloverAt = self.computeRollover(current_time)


def setup_logging(level: str, service_name: str, log_directory: str) -> logging.Logger:
    """
    Configure structured JSON logging for a service.

    Logs to both stdout and a daily rotating file in log_directory.
    File naming: YYYY-MM-DD.log.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        service_name: Name of the service for logger identification
        log_directory: Directory for rotating log files

    Returns:
        Configured logger instance

    Raises:
        ValueError: If level is not a valid log level
    """
    level_upper = level.upper()
    if level_upper not in VALID_LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level}. Must be one of {sorted(VALID_LOG_LEVELS)}")

    numeric_level = getattr(logging, level_upper)

    logger = logging.getLogger(service_name)
    logger.setLevel(numeric_level)
    logger.handlers.clear()

    formatter = JSONFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(numeric_level)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    os.makedirs(log_directory, exist_ok=True)
    file_handler = DailyRotatingFileHandler(directory=log_directory)
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def get_service_logger(service_name: str) -> logging.Logger:
    """
    Get root logger for a service.

    Args:
        service_name: Base service logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(service_name)


def get_named_logger(service_name: str, logger_name: str) -> logging.Logger:
    """
    Get a named logger under a service namespace.

    Args:
        service_name: Base service logger name
        logger_name: Module or component name

    Returns:
        Logger instance
    """
    full_name = f"{service_name}.{logger_name}"
    return logging.getLogger(full_name)
