"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from court_service.config import get_settings
from court_service.core.state import init_app_state
from court_service.logging import get_logger, setup_logging
from court_service.services.central_bank_client import CentralBankClient
from court_service.services.dispute_service import DisputeService
from court_service.services.identity_client import IdentityClient
from court_service.services.platform_signer import PlatformSigner
from court_service.services.reputation_client import ReputationClient
from court_service.services.task_board_client import TaskBoardClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage app startup and shutdown."""
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    db_path = settings.database.path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    state.dispute_service = DisputeService(db_path=db_path)

    state.identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        timeout_seconds=settings.identity.timeout_seconds,
    )

    signer: PlatformSigner | None = None
    if settings.platform.private_key_path is not None:
        private_key_path = Path(settings.platform.private_key_path)
        if private_key_path.exists():
            signer = PlatformSigner(
                private_key_path=str(private_key_path),
                platform_agent_id=settings.platform.agent_id,
            )
        else:
            logger.warning(
                "Platform signer key file not found; downstream signed clients not initialized",
                extra={"private_key_path": str(private_key_path)},
            )
    state.platform_signer = signer

    if settings.task_board is not None and signer is not None:
        state.task_board_client = TaskBoardClient(
            base_url=settings.task_board.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )
    if settings.central_bank is not None and signer is not None:
        state.central_bank_client = CentralBankClient(
            base_url=settings.central_bank.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )
    if settings.reputation is not None and signer is not None:
        state.reputation_client = ReputationClient(
            base_url=settings.reputation.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )

    state.judges = []

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield

    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    if state.identity_client is not None:
        await state.identity_client.close()
    if state.task_board_client is not None:
        await state.task_board_client.close()
    if state.central_bank_client is not None:
        await state.central_bank_client.close()
    if state.reputation_client is not None:
        await state.reputation_client.close()
    if state.dispute_service is not None:
        state.dispute_service.close()
