"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import httpx

from observatory_service.config import get_settings
from observatory_service.core.state import init_app_state
from observatory_service.logging import get_logger, setup_logging
from observatory_service.services.demo_signer import bootstrap_demo_agent

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
    except Exception:
        logger.warning(
            "Database not available at startup",
            extra={"path": settings.database.path},
        )

    # Initialize demo proxy
    try:
        demo_settings = settings.demo
        state.demo_signer = await bootstrap_demo_agent(
            identity_url=demo_settings.identity_url,
            central_bank_url=demo_settings.central_bank_url,
            platform_key_path=Path(demo_settings.platform_key_path),
            keys_dir=Path(demo_settings.keys_dir),
            human_agent_name=demo_settings.human_agent_name,
            human_initial_balance=demo_settings.human_initial_balance,
            timeout_seconds=demo_settings.timeout_seconds,
        )
        state.task_board_client = httpx.AsyncClient(
            timeout=float(demo_settings.timeout_seconds),
        )
        logger.info("Demo proxy initialized", extra={"agent_id": state.demo_signer.human_agent_id})
    except Exception:
        logger.warning("Demo proxy not available (services may not be running)", exc_info=True)

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
    if state.task_board_client is not None:
        await state.task_board_client.aclose()
        logger.info("Task board client closed")
    if state.db is not None:
        await state.db.close()
        logger.info("Database connection closed")
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
