"""Application lifecycle management."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from base_agent.factory import AgentFactory

from court_service.config import get_config_path, get_settings
from court_service.core.state import init_app_state
from court_service.judges import LLMJudge, MockJudge
from court_service.logging import get_logger, setup_logging
from court_service.services.dispute_db_client import DisputeDbClient
from court_service.services.dispute_service import DisputeService
from court_service.services.ruling_orchestrator import RulingOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from court_service.config import Settings
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
                    fixed_worker_pct=50,
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

    store: DisputeStorageInterface
    if settings.db_gateway is None:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)

    store = DisputeDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )
    state.store = store
    orchestrator = RulingOrchestrator(store=store)
    state.dispute_service = DisputeService(store=store, orchestrator=orchestrator)

    # Instantiate the platform agent from the agent config.
    # This loads the platform's Ed25519 keypair and registers with Identity.
    if settings.platform.agent_config_path:
        config_path = Path(settings.platform.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path(get_config_path()).parent / config_path

        factory = AgentFactory(config_path=config_path)
        platform_agent = factory.platform_agent()
        await platform_agent.register()
        state.platform_agent = platform_agent

        if platform_agent.agent_id is None:
            msg = "Platform agent registration did not return an agent_id"
            raise RuntimeError(msg)

        settings.platform.agent_id = platform_agent.agent_id
        logger.info("Platform agent registered", extra={"agent_id": platform_agent.agent_id})

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

    yield

    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    if state.platform_agent is not None:
        await state.platform_agent.close()
    if state.dispute_service is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.dispute_service.close()
