"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from identity_service.config import get_settings
from identity_service.core.state import init_app_state
from identity_service.logging import get_logger, setup_logging
from identity_service.services.agent_registry import AgentRegistry

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

    # Ensure database directory exists
    db_path = settings.database.path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # Initialize agent registry with database
    state.registry = AgentRegistry(
        db_path=db_path,
        algorithm=settings.crypto.algorithm,
        public_key_prefix=settings.crypto.public_key_prefix,
        public_key_bytes=settings.crypto.public_key_bytes,
        signature_bytes=settings.crypto.signature_bytes,
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
    state.registry.close()
