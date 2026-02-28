"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aiosqlite

from observatory_service.config import get_settings
from observatory_service.core.state import init_app_state
from observatory_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name)
    logger = get_logger(__name__)

    state = init_app_state()

    # Open read-only database connection
    db_uri = f"file:{settings.database.path}?mode=ro"
    try:
        db = await aiosqlite.connect(db_uri, uri=True)
        db.row_factory = aiosqlite.Row
        state.db = db
        logger.info("Database connection opened", extra={"path": settings.database.path})
    except Exception:
        logger.warning(
            "Database not available at startup",
            extra={"path": settings.database.path},
        )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    if state.db is not None:
        await state.db.close()
        logger.info("Database connection closed")
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
