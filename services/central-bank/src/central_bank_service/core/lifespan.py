"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.factory import AgentFactory
from service_clients.identity import IdentityClient

from central_bank_service.config import get_config_path, get_settings
from central_bank_service.core.state import init_app_state
from central_bank_service.logging import get_logger, setup_logging
from central_bank_service.services.ledger_db_client import LedgerDbClient

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

    # Initialize ledger. db_gateway is a required config field (Pydantic enforces its
    # presence at load); guard against an empty URL so startup still fails fast.
    if not settings.db_gateway.url:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)

    state.ledger = LedgerDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )

    # Initialize identity client
    identity_config = settings.identity
    state.identity_client = IdentityClient(
        base_url=identity_config.base_url,
        get_agent_path=identity_config.get_agent_path,
        verify_jws_path=identity_config.verify_jws_path,
        timeout_seconds=identity_config.timeout_seconds,
    )
    # Platform identity is resolved from the registered PlatformAgent only.
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
    await state.ledger.close()
