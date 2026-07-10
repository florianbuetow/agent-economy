"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite
import httpx
from service_auth import AgentFactory

from ui_service.config import get_settings
from ui_service.core.state import init_app_state
from ui_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    import logging
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


async def _fund_platform_treasury(
    factory: AgentFactory,
    treasury_id: str,
    balance: int,
    logger: logging.Logger,
) -> None:
    """Mint the shared platform/user-agent treasury account.

    The user agent shares the platform identity (authorized to mint its own
    balance), so a UI-posted task can lock escrow from this account. Without
    it, escrow_lock fails with account_not_found — surfaced to the UI as a
    misleading 404 on POST /tasks.
    """
    platform_agent = factory.platform_agent()
    # Carry the already-registered identity so the signed token's JWS kid verifies.
    platform_agent.agent_id = treasury_id
    try:
        await platform_agent.create_account(agent_id=treasury_id, initial_balance=balance)
        logger.info(
            "Platform treasury funded",
            extra={"agent_id": treasury_id, "balance": balance},
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:  # 409: account already exists
            raise
        logger.info("Platform treasury account already exists", extra={"agent_id": treasury_id})
    finally:
        await platform_agent.close()


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
    factory: AgentFactory | None = None
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
            extra={"agent_id": user_agent.agent_id, "agent_name": user_agent.name},
        )
    except Exception as exc:
        logger.error(
            "UserAgent initialization failed — proxy endpoints will be unavailable",
            extra={"error": str(exc)},
        )

    # Mint the platform treasury so UI-driven task posting can lock escrow.
    # Kept separate from registration: a funding failure disables only task
    # posting, not the dashboard or the rest of the proxy.
    agent = state.user_agent
    if factory is not None and agent is not None and agent.agent_id is not None:
        try:
            await _fund_platform_treasury(
                factory,
                agent.agent_id,
                settings.user_agent.treasury_balance,
                logger,
            )
        except Exception as exc:
            logger.error(
                "Platform treasury funding failed — task posting may be unavailable",
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
