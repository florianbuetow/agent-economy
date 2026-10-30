"""Shared FastAPI dependencies for the UI service."""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import Depends
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state
from ui_service.services.agents import QualityReader
from ui_service.services.task_board_reads import TaskBoardReader


def get_db() -> aiosqlite.Connection:
    """Return the live database connection.

    Raises:
        ServiceError: ``503 database_unavailable`` when the database has not
            been initialised yet.
    """
    state = get_app_state()
    db = state.db
    if db is None:
        raise ServiceError(
            error="database_unavailable",
            message="Database not available yet",
            status_code=503,
            details=None,
        )
    return db


DbConn = Annotated[aiosqlite.Connection, Depends(get_db)]
"""Injectable database connection that yields a 503 when unavailable."""


def get_task_board_reader() -> TaskBoardReader:
    """Return the live authoritative Task Board reader."""
    reader = get_app_state().task_board_reader
    if reader is None:
        raise ServiceError(
            error="task_board_reader_unavailable",
            message="Task Board reader not available yet",
            status_code=503,
            details=None,
        )
    return reader


TaskBoardRead = Annotated[TaskBoardReader, Depends(get_task_board_reader)]


def get_quality_reader() -> QualityReader:
    """Return the Reputation quality reader (G12)."""
    reader = get_app_state().quality_reader
    if reader is None:
        raise ServiceError(
            error="quality_reader_unavailable",
            message="Reputation quality reader not available yet",
            status_code=503,
            details=None,
        )
    return reader


QualityRead = Annotated[QualityReader, Depends(get_quality_reader)]
