"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
from base_agent import AgentFactory

from ui_service.config import get_settings
from ui_service.core.state import init_app_state
from ui_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    # Open read-only database connection
    db_uri = f"file:{settings.database.path}?mode=ro"
    try:
        db = await aiosqlite.connect(db_uri, uri=True)
        db.row_factory = aiosqlite.Row
        state.db = db
        logger.info("Database connection opened", extra={"path": settings.database.path})
    except (OSError, aiosqlite.Error) as exc:
        logger.error(
            "Database not available at startup",
            extra={"path": settings.database.path, "error": str(exc)},
        )

    # Initialize UserAgent for UI-driven task operations
    try:
        config_path = Path(settings.user_agent.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        factory = AgentFactory(config_path=config_path.resolve())
        user_agent = factory.user_agent()
        await user_agent.register()
        state.user_agent = user_agent
        logger.info(
            "UserAgent initialized",
            extra={"agent_id": user_agent.agent_id, "name": user_agent.name},
        )
    except Exception as exc:
        logger.error(
            "UserAgent initialization failed — proxy endpoints will be unavailable",
            extra={"error": str(exc)},
        )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
            "web_root": settings.frontend.web_root,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    if state.user_agent is not None:
        await state.user_agent.close()
        logger.info("UserAgent closed")
    if state.db is not None:
        await state.db.close()
        logger.info("Database connection closed")
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
