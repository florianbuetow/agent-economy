"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from court_service.config import get_settings
from court_service.core.state import init_app_state
from court_service.judges import LLMJudge, MockJudge
from court_service.logging import get_logger, setup_logging
from court_service.services.central_bank_client import CentralBankClient
from court_service.services.dispute_service import DisputeService
from court_service.services.dispute_store import DisputeStore
from court_service.services.identity_client import IdentityClient
from court_service.services.platform_signer import PlatformSigner
from court_service.services.reputation_client import ReputationClient
from court_service.services.ruling_orchestrator import RulingOrchestrator
from court_service.services.task_board_client import TaskBoardClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from logging import Logger

    from fastapi import FastAPI

    from court_service.config import Settings
    from court_service.core.state import AppState
    from court_service.judges import Judge


def _init_platform_signer(settings: Settings, logger: Logger) -> PlatformSigner | None:
    if settings.platform.private_key_path is None:
        return None

    private_key_path = Path(settings.platform.private_key_path)
    if not private_key_path.exists():
        logger.warning(
            "Platform signer key file not found; downstream signed clients not initialized",
            extra={"private_key_path": str(private_key_path)},
        )
        return None

    return PlatformSigner(
        private_key_path=str(private_key_path),
        platform_agent_id=settings.platform.agent_id,
    )


def _init_downstream_clients(
    state: AppState,
    settings: Settings,
    signer: PlatformSigner | None,
) -> None:
    if signer is None:
        return

    if settings.task_board is not None:
        state.task_board_client = TaskBoardClient(
            base_url=settings.task_board.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )
    if settings.central_bank is not None:
        state.central_bank_client = CentralBankClient(
            base_url=settings.central_bank.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )
    if settings.reputation is not None:
        state.reputation_client = ReputationClient(
            base_url=settings.reputation.base_url,
            signer=signer,
            timeout_seconds=settings.identity.timeout_seconds,
        )


def _build_judges(settings: Settings) -> list[Judge]:
    judges: list[Judge] = []
    for judge_cfg in settings.judges.judges:
        provider = (judge_cfg.provider or "llm").lower()
        if provider == "mock":
            judges.append(
                MockJudge(
                    judge_id=judge_cfg.id,
                    fixed_worker_pct=50,
                    reasoning="Mock judge default reasoning.",
                )
            )
            continue

        if judge_cfg.temperature is None:
            msg = f"Judge {judge_cfg.id} is missing required temperature"
            raise ValueError(msg)
        judges.append(
            LLMJudge(
                judge_id=judge_cfg.id,
                model=judge_cfg.model,
                temperature=judge_cfg.temperature,
            )
        )

    if len(judges) != settings.judges.panel_size:
        msg = "INVALID_PANEL_SIZE: configured judge count does not match panel_size"
        raise ValueError(msg)

    return judges


async def _close_resources(state: AppState) -> None:
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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage app startup and shutdown."""
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    db_path = settings.database.path
    store = DisputeStore(db_path=db_path)
    orchestrator = RulingOrchestrator(store=store)
    state.dispute_service = DisputeService(store=store, orchestrator=orchestrator)

    state.identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        timeout_seconds=settings.identity.timeout_seconds,
    )

    signer = _init_platform_signer(settings, logger)
    state.platform_signer = signer

    _init_downstream_clients(state, settings, signer)
    state.judges = _build_judges(settings)

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

    await _close_resources(state)
