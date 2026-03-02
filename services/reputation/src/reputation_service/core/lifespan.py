"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from base_agent.factory import AgentFactory

from reputation_service.config import get_config_path, get_settings
from reputation_service.core.state import init_app_state
from reputation_service.logging import get_logger, setup_logging
from reputation_service.services.feedback_store import FeedbackStore

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

    # Initialize SQLite-backed feedback store
    state.feedback_store = FeedbackStore(db_path=settings.database.path)

    # Initialize platform agent for local token verification
    if settings.platform.agent_config_path:
        config_path = Path(settings.platform.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path(get_config_path()).parent / config_path

        factory = AgentFactory(config_path=config_path)
        platform_agent = factory.platform_agent()
        await platform_agent.register()
        state.platform_agent = platform_agent

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
    if state.platform_agent is not None:  # pyright: ignore[reportUnnecessaryComparison]
        await state.platform_agent.close()
