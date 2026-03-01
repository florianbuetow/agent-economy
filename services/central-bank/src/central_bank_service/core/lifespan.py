"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from base_agent.factory import AgentFactory

from central_bank_service.config import get_config_path, get_settings
from central_bank_service.core.state import init_app_state
from central_bank_service.logging import get_logger, setup_logging
from central_bank_service.services.identity_client import IdentityClient
from central_bank_service.services.ledger import Ledger

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

    # Initialize ledger
    state.ledger = Ledger(db_path=db_path)

    # Initialize identity client
    state.identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        get_agent_path=settings.identity.get_agent_path,
    )

    if settings.platform.agent_config_path:
        config_path = Path(settings.platform.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path(get_config_path()).parent / config_path
        factory = AgentFactory(config_path=config_path)
        state.platform_agent = factory.platform_agent()
        await state.platform_agent.register()
        logger.info("Platform agent registered", extra={"agent_id": state.platform_agent.agent_id})

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
    if state.platform_agent is not None:
        await state.platform_agent.close()
    await state.identity_client.close()
    state.ledger.close()
