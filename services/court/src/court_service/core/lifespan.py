"""Application lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.factory import AgentFactory
from service_auth.gateway import GatewayRequestSigner

from court_service.config import get_config_path, get_settings
from court_service.core.state import init_app_state
from court_service.judges import LLMJudge, MockJudge
from court_service.logging import get_logger, setup_logging
from court_service.routers.disputes import load_task_for_ruling
from court_service.services.dispute_db_client import DisputeDbClient
from court_service.services.dispute_service import DisputeService
from court_service.services.evidence_bundle import EvidencePolicy
from court_service.services.ruling_orchestrator import DeliverableFetcher, RulingOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from court_service.config import Settings
    from court_service.core.state import AppState
    from court_service.judges import Judge
    from court_service.services.protocol import DisputeStorageInterface


def _build_judges(settings: Settings) -> list[Judge]:
    judges: list[Judge] = []
    for judge_cfg in settings.judges.judges:
        provider = (judge_cfg.provider or "llm").lower()
        if provider == "mock":
            judges.append(
                MockJudge(
                    judge_id=judge_cfg.id,
                    fixed_worker_pct=settings.judges.mock_worker_pct,
                    spec_quality=settings.judges.mock_spec_quality,
                    delivery_quality=settings.judges.mock_delivery_quality,
                    reasoning="Mock judge default reasoning.",
                )
            )
            continue

        if judge_cfg.temperature is None:
            msg = f"Judge {judge_cfg.id} is missing required temperature"
            raise ValueError(msg)
        api_key: str | None = None
        if judge_cfg.api_key_env is not None:
            api_key = os.environ.get(judge_cfg.api_key_env)
            if not api_key:
                msg = (
                    f"Judge {judge_cfg.id} requires env var"
                    f" {judge_cfg.api_key_env} but it is not set"
                )
                raise ValueError(msg)

        judges.append(
            LLMJudge(
                judge_id=judge_cfg.id,
                model=judge_cfg.model,
                temperature=judge_cfg.temperature,
                api_base=judge_cfg.api_base,
                api_key=api_key,
            )
        )

    if len(judges) != settings.judges.panel_size:
        msg = "INVALID_PANEL_SIZE: configured judge count does not match panel_size"
        raise ValueError(msg)

    return judges


async def _reclaim_loop(state: AppState, interval_seconds: int) -> None:
    """Periodically resume rulings whose judging lease expired or was released.

    Task Board's periodic trigger stops once a task leaves ``disputed``, so a
    ruling whose Task Board settlement landed but whose later delivery failed is
    only ever finished by this sweep (plan item 0.3).
    """
    logger = get_logger(__name__)
    while True:
        await asyncio.sleep(interval_seconds)
        if state.dispute_service is None or state.platform_agent is None:
            continue
        try:
            resumed = await state.dispute_service.reclaim_stalled_rulings(
                state.judges if state.judges is not None else [],
                state.platform_agent,
                load_task_for_ruling,
            )
        except Exception:
            logger.exception("Ruling reclaim sweep failed")
            continue
        if resumed:
            logger.info("Resumed stalled rulings", extra={"dispute_ids": resumed})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage app startup and shutdown."""
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()
    state.rebuttal_deadline_seconds = settings.disputes.rebuttal_deadline_seconds
    state.max_claim_length = settings.disputes.max_claim_length
    state.max_rebuttal_length = settings.disputes.max_rebuttal_length

    # Validate required, pre-provisioned credentials before opening clients.
    if settings.db_gateway is None:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)
    gateway_signer = GatewayRequestSigner(
        settings.db_gateway.signing,
        config_dir=Path(get_config_path()).parent,
    )
    config_path = Path(settings.platform.agent_config_path)
    if not config_path.is_absolute():
        config_path = Path(get_config_path()).parent / config_path
    factory = AgentFactory(config_path=config_path)
    platform_agent = factory.platform_agent()
    state.platform_agent = platform_agent

    store: DisputeStorageInterface
    store = DisputeDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
        signer=gateway_signer,
    )
    state.store = store
    orchestrator = RulingOrchestrator(
        store=store,
        feedback_extremely_satisfied_cutoff=settings.disputes.feedback_extremely_satisfied_cutoff,
        feedback_satisfied_cutoff=settings.disputes.feedback_satisfied_cutoff,
        feedback_comment_max_length=settings.disputes.feedback_comment_max_length,
        judging_lease_seconds=settings.disputes.judging_lease_seconds,
    )
    state.dispute_service = DisputeService(store=store, orchestrator=orchestrator)

    await platform_agent.register()

    if platform_agent.agent_id is None:
        msg = "Platform agent registration did not return an agent_id"
        raise RuntimeError(msg)

    settings.platform.agent_id = platform_agent.agent_id
    logger.info("Platform agent registered", extra={"agent_id": platform_agent.agent_id})

    state.deliverable_fetcher = DeliverableFetcher(
        task_board_url=platform_agent.config.task_board_url,
        policy=EvidencePolicy(
            max_total_bytes=settings.evidence.max_total_bytes,
            max_file_bytes=settings.evidence.max_file_bytes,
            allowed_extensions=frozenset(settings.evidence.allowed_extensions),
            allowed_content_types=frozenset(settings.evidence.allowed_content_types),
        ),
        timeout_seconds=settings.db_gateway.timeout_seconds,
        signer=platform_agent,
    )

    state.judges = _build_judges(settings)

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
            "platform_agent_id": settings.platform.agent_id,
        },
    )

    reclaim_task = asyncio.create_task(
        _reclaim_loop(state, settings.disputes.reclaim_interval_seconds)
    )

    yield

    reclaim_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reclaim_task

    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    await state.platform_agent.close()
    if isinstance(  # pyright: ignore[reportUnnecessaryIsInstance]
        state.deliverable_fetcher,
        DeliverableFetcher,
    ):
        await state.deliverable_fetcher.close()
    if state.dispute_service is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.dispute_service.close()
