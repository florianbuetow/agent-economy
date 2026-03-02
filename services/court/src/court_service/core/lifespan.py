"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from base_agent.factory import AgentFactory

from court_service.config import get_config_path, get_settings
from court_service.core.state import init_app_state
from court_service.judges import LLMJudge, MockJudge
from court_service.logging import get_logger, setup_logging
from court_service.services.dispute_service import DisputeService
from court_service.services.dispute_store import DisputeStore
from court_service.services.identity_client import IdentityClient
from court_service.services.ruling_orchestrator import RulingOrchestrator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from court_service.config import Settings
    from court_service.judges import Judge


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
    if state.identity_client is not None:  # pyright: ignore[reportUnnecessaryComparison]
        await state.identity_client.close()
    if state.dispute_service is not None:  # pyright: ignore[reportUnnecessaryComparison]
        state.dispute_service.close()
