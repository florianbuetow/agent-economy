"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.factory import AgentFactory
from service_clients.identity import IdentityClient

from reputation_service.config import get_config_path, get_settings
from reputation_service.core.state import init_app_state
from reputation_service.logging import get_logger, setup_logging
from reputation_service.services.feedback_db_client import FeedbackDbClient
from reputation_service.services.platform_identity_client import PlatformJwsVerifier

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
    state.feedback_reveal_timeout_seconds = settings.feedback.reveal_timeout_seconds
    state.feedback_max_comment_length = settings.feedback.max_comment_length

    # db_gateway is a required config field (Pydantic enforces its presence at load);
    # guard against an empty URL so startup still fails fast on bad db_gateway config.
    if not settings.db_gateway.url:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)

    store = FeedbackDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )
    state.feedback_store = store

    # Initialize platform agent for local token verification
    if settings.platform.agent_config_path:
        config_path = Path(settings.platform.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path(get_config_path()).parent / config_path

        factory = AgentFactory(config_path=config_path)
        platform_agent = factory.platform_agent()
        await platform_agent.register()
        state.platform_agent = platform_agent

    # Two-tier verification. Platform operations (force_visible feedback) verify locally
    # via the platform agent — no Identity round-trip, so they survive Identity outages.
    state.platform_verifier = PlatformJwsVerifier(
        platform_agent_provider=lambda: state.platform_agent,
    )

    # Agent feedback operations verify via Identity. When Identity is not configured,
    # fall back to local platform verification for all callers.
    if settings.identity is not None:
        identity_config = settings.identity
        state.identity_client = IdentityClient(
            base_url=identity_config.base_url,
            get_agent_path=identity_config.get_agent_path or "",
            verify_jws_path=identity_config.verify_jws_path,
            timeout_seconds=identity_config.timeout_seconds or 10,
        )
    else:
        state.identity_client = state.platform_verifier

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
    if state.identity_client is not None:  # pyright: ignore[reportUnnecessaryComparison]
        await state.identity_client.close()
    if state.feedback_store is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.feedback_store.close()
    if state.platform_agent is not None:  # pyright: ignore[reportUnnecessaryComparison]
        await state.platform_agent.close()
