"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx

from reputation_service.config import get_settings
from reputation_service.core.state import init_app_state
from reputation_service.logging import get_logger, setup_logging
from reputation_service.services.feedback_store import FeedbackStore
from reputation_service.services.identity_client import IdentityClient

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

    # Initialize SQLite-backed feedback store
    state.feedback_store = FeedbackStore(db_path=settings.database.path)

    # Initialize identity client
    state.identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        timeout_seconds=settings.identity.timeout_seconds,
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
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
    if state.feedback_store is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.feedback_store.close()
    if state.identity_client is not None:  # pyright: ignore[reportUnnecessaryComparison]
        try:
            await state.identity_client.close()
        except (httpx.HTTPError, OSError):
            logger.warning("Failed to close identity client during shutdown", exc_info=True)
