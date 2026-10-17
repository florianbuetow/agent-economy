"""Application lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.factory import AgentFactory
from service_auth.gateway import GatewayRequestSigner
from service_clients.bank import BankClient
from service_clients.identity import IdentityClient
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock

from task_board_service.config import get_config_path, get_settings, resolve_recovery_path
from task_board_service.core.state import init_app_state
from task_board_service.logging import get_logger, setup_logging
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_db_client import TaskDbClient
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.token_validator import TokenValidator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from task_board_service.services.protocol import TaskStorageInterface


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    settings = get_settings()
    lock_path = resolve_recovery_path(settings.recovery.maintenance_lock_path)

    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.SHARED,
        blocking=False,
    ):
        setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
        logger = get_logger(__name__)
        state = init_app_state()
        platform_agent = None
        identity_client = None
        central_bank_client = None
        store: TaskStorageInterface | None = None
        task_manager = None
        deadline_sweep_task: asyncio.Task[None] | None = None
        try:
            # db_gateway configuration is required by Settings and has no fallback.
            gateway_signer = GatewayRequestSigner(
                settings.db_gateway.signing,
                config_dir=Path(get_config_path()).parent,
            )

            db_path = settings.database.path
            db_directory = Path(db_path).parent
            if settings.assets is not None:
                asset_storage_path = settings.assets.storage_path
                max_file_size = settings.assets.max_file_size
                max_files_per_task = settings.assets.max_files_per_task
            elif settings.limits is not None:
                asset_storage_path = str(db_directory / "assets")
                max_file_size = settings.limits.max_file_size
                max_files_per_task = settings.limits.max_assets_per_task
            else:
                msg = "Either 'assets' or legacy 'limits' config section must be present"
                raise RuntimeError(msg)
            Path(asset_storage_path).mkdir(parents=True, exist_ok=True)

            config_path = Path(settings.platform.agent_config_path)
            if not config_path.is_absolute():
                config_path = Path(get_config_path()).parent / config_path

            factory = AgentFactory(config_path=config_path)
            platform_agent = factory.platform_agent()
            await platform_agent.register()
            if platform_agent.agent_id is None:
                msg = "Platform agent registration did not return an agent_id"
                raise RuntimeError(msg)
            platform_agent_id = platform_agent.agent_id
            state.platform_agent = platform_agent
            state.platform_signer = platform_agent
            logger.info("Platform agent registered", extra={"agent_id": platform_agent_id})

            identity_client = IdentityClient(
                base_url=settings.identity.base_url,
                get_agent_path=settings.identity.get_agent_path,
                verify_jws_path=settings.identity.verify_jws_path,
                timeout_seconds=settings.identity.timeout_seconds,
            )
            state.identity_client = identity_client

            central_bank_client = BankClient(
                base_url=settings.central_bank.base_url,
                escrow_lock_path=settings.central_bank.escrow_lock_path,
                escrow_release_path=settings.central_bank.escrow_release_path,
                escrow_split_path=settings.central_bank.escrow_split_path,
                timeout_seconds=settings.central_bank.timeout_seconds,
                platform_signer=platform_agent,
            )
            state.central_bank_client = central_bank_client

            store = TaskDbClient(
                base_url=settings.db_gateway.url,
                timeout_seconds=settings.db_gateway.timeout_seconds,
                signer=gateway_signer,
            )
            state.store = store
            escrow_coordinator = EscrowCoordinator(
                central_bank_client=central_bank_client,
                store=store,
            )
            state.escrow_coordinator = escrow_coordinator
            token_validator = TokenValidator(
                platform_agent=platform_agent,
                identity_client=identity_client,
            )
            state.token_validator = token_validator
            deadline_evaluator = DeadlineEvaluator(
                store=store,
                escrow_coordinator=escrow_coordinator,
                platform_agent=state.platform_agent,
            )

            if settings.deadline_evaluation is None:
                msg = "deadline_evaluation configuration is required"
                raise RuntimeError(msg)
            deadline_sweep_task = asyncio.create_task(
                deadline_evaluator.run_periodic_sweep(
                    settings.deadline_evaluation.evaluation_interval_seconds
                )
            )
            state.deadline_sweep_task = deadline_sweep_task
            asset_manager = AssetManager(
                store=store,
                token_validator=token_validator,
                deadline_evaluator=deadline_evaluator,
                asset_storage_path=asset_storage_path,
                max_file_size=max_file_size,
                max_files_per_task=max_files_per_task,
            )
            state.asset_manager = asset_manager
            task_manager = TaskManager(
                store=store,
                central_bank_client=central_bank_client,
                escrow_coordinator=escrow_coordinator,
                token_validator=token_validator,
                deadline_evaluator=deadline_evaluator,
                asset_manager=asset_manager,
                platform_signer=platform_agent,
                platform_agent_id=platform_agent_id,
            )
            state.task_manager = task_manager

            logger.info(
                "Service starting",
                extra={
                    "service": settings.service.name,
                    "version": settings.service.version,
                    "port": settings.server.port,
                    "db_path": db_path,
                    "asset_storage_path": asset_storage_path,
                    "central_bank_base_url": settings.central_bank.base_url,
                    "platform_agent_id": platform_agent_id,
                },
            )

            yield
        finally:
            logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
            if deadline_sweep_task is not None:
                deadline_sweep_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await deadline_sweep_task
            if task_manager is not None:
                task_manager.close()
            elif store is not None:
                store.close()
            if platform_agent is not None:
                await platform_agent.close()
            if central_bank_client is not None:
                await central_bank_client.close()
            if identity_client is not None:
                await identity_client.close()
