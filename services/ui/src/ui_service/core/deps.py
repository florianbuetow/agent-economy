"""Shared FastAPI dependencies for the UI service."""

from __future__ import annotations

from typing import Annotated

import aiosqlite
from fastapi import Depends
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state


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
