"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from identity_service.config import get_settings
from identity_service.core.state import init_app_state
from identity_service.logging import get_logger, setup_logging
from identity_service.services.agent_db_client import AgentDbClient
from identity_service.services.agent_registry import AgentRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from identity_service.services.protocol import IdentityStorageInterface


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    store: IdentityStorageInterface
    if settings.db_gateway is None:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)

    store = AgentDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )

    # Initialize agent registry with gateway-backed store.
    state.registry = AgentRegistry(
        store=store,
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
    await state.registry.close()
