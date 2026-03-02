"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

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

    init_app_state()

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
    logger.info("Service shutting down")
