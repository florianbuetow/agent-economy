"""Application lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from service_auth.factory import AgentFactory
from service_auth.gateway import GatewayRequestSigner
from service_clients.bank import BankClient
from service_clients.identity import IdentityClient
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock

from task_board_service.config import get_config_path, get_settings, resolve_recovery_path
from task_board_service.core.state import init_app_state
from task_board_service.logging import get_logger, setup_logging
from task_board_service.operator_authority import load_operator_asset_authority
from task_board_service.services.artifact_maintenance import ArtifactMaintenance
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.financial_effect_recovery import (
    FinancialEffectRecovery,
    RecoveryBank,
    RecoveryStore,
)
from task_board_service.services.posting_recovery import PostingRecovery
from task_board_service.services.task_db_client import TaskDbClient
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.token_validator import TokenValidator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Coroutine
    from typing import Any

    from fastapi import FastAPI

    from task_board_service.core.state import AppState
    from task_board_service.services.protocol import TaskStorageInterface


SupervisedLoop = Literal[
    "deadline_sweep_task",
    "posting_recovery_task",
    "financial_effect_recovery_task",
    "artifact_maintenance_task",
]


def supervise_loop(
    state: AppState,
    attr: SupervisedLoop,
    loop_factory: Callable[[], Coroutine[Any, Any, None]],
    *,
    restart_delay_seconds: float,
) -> asyncio.Task[None]:
    """Start a background loop, store it on ``state``, and restart it if it ever stops.

    Cancellation is a deliberate stop and is never restarted. Any other exit (an
    escaped exception or an unexpected return) is logged and the loop is re-created
    after ``restart_delay_seconds``, unless the service is shutting down.
    """
    return _start_supervised(state, attr, loop_factory, restart_delay_seconds, initial_delay=0)


def _start_supervised(
    state: AppState,
    attr: SupervisedLoop,
    loop_factory: Callable[[], Coroutine[Any, Any, None]],
    restart_delay_seconds: float,
    *,
    initial_delay: float,
) -> asyncio.Task[None]:
    async def run() -> None:
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        await loop_factory()

    task = asyncio.create_task(run(), name=attr)
    setattr(state, attr, task)

    def on_done(done: asyncio.Task[None]) -> None:
        if done.cancelled():
            return
        logger = get_logger(__name__)
        error = done.exception()
        if state.shutting_down or getattr(state, attr) is not done:
            if error is not None:
                logger.error(
                    "Background loop stopped during shutdown",
                    exc_info=error,
                    extra={"loop": attr},
                )
            return
        logger.error(
            "Background loop stopped unexpectedly; restarting",
            exc_info=error,
            extra={"loop": attr, "restart_delay_seconds": restart_delay_seconds},
        )
        _start_supervised(
            state,
            attr,
            loop_factory,
            restart_delay_seconds,
            initial_delay=restart_delay_seconds,
        )

    task.add_done_callback(on_done)
    return task


async def _stop_loop(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


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
        recovery_store: TaskDbClient | None = None
        task_manager = None
        asset_manager: AssetManager | None = None
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
            config_path = Path(settings.platform.agent_config_path)
            if not config_path.is_absolute():
                config_path = Path(get_config_path()).parent / config_path

            factory = AgentFactory(config_path=config_path)
            operator_asset_authority = load_operator_asset_authority(config_path)
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
            )
            state.escrow_coordinator = escrow_coordinator
            token_validator = TokenValidator(
                platform_agent=platform_agent,
                identity_client=identity_client,
            )
            token_validator.configure_operator_asset_authority(operator_asset_authority)
            state.token_validator = token_validator
            if settings.deadline_evaluation is None:
                msg = "deadline_evaluation configuration is required"
                raise RuntimeError(msg)
            deadline_evaluator = DeadlineEvaluator(
                store=store,
                escrow_coordinator=escrow_coordinator,
                platform_agent=state.platform_agent,
                deadline_actor_id=settings.deadline_evaluation.actor_id,
            )
            sweep_interval = settings.deadline_evaluation.evaluation_interval_seconds
            supervise_loop(
                state,
                "deadline_sweep_task",
                lambda: deadline_evaluator.run_periodic_sweep(sweep_interval),
                restart_delay_seconds=sweep_interval,
            )
            asset_manager = AssetManager(
                store=store,
                token_validator=token_validator,
                deadline_evaluator=deadline_evaluator,
                asset_storage_path=asset_storage_path,
                max_file_size=max_file_size,
                max_files_per_task=max_files_per_task,
            )
            state.asset_manager = asset_manager
            # The shipped config.yaml always carries `artifacts`; a config without the
            # section (legacy test fixtures) keeps every task on the V5 board_assets path.
            artifact_settings = settings.artifacts
            if artifact_settings is not None:
                asset_manager.configure_artifacts(
                    store,
                    {
                        "contract_version": 1,
                        "max_file_bytes": max_file_size,
                        "max_files_per_manifest": max_files_per_task,
                        "max_manifest_bytes": artifact_settings.max_manifest_bytes,
                        "reservation_ttl_seconds": artifact_settings.reservation_ttl_seconds,
                        "reservation_renew_before_expiry_seconds": (
                            artifact_settings.reservation_renew_before_expiry_seconds
                        ),
                        "max_upload_duration_seconds": (
                            artifact_settings.max_upload_duration_seconds
                        ),
                    },
                )
                artifact_maintenance = ArtifactMaintenance(
                    asset_manager,
                    stale_file_seconds=artifact_settings.stale_file_seconds,
                    archive_after_seconds=artifact_settings.archive_after_seconds,
                    archive_batch_size=artifact_settings.archive_batch_size,
                )
                artifact_interval = artifact_settings.recovery_interval_seconds
                supervise_loop(
                    state,
                    "artifact_maintenance_task",
                    lambda: artifact_maintenance.run_periodic(artifact_interval),
                    restart_delay_seconds=artifact_interval,
                )
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
            posting_recovery = PostingRecovery(
                store=store,
                bank=central_bank_client,
                batch_size=settings.posting_recovery.batch_size,
                diagnostic_limit=settings.posting_recovery.diagnostic_limit,
            )
            state.posting_recovery = posting_recovery
            posting_interval = settings.posting_recovery.interval_seconds
            supervise_loop(
                state,
                "posting_recovery_task",
                lambda: posting_recovery.run_periodic(interval_seconds=posting_interval),
                restart_delay_seconds=posting_interval,
            )
            recovery_store = TaskDbClient(
                base_url=settings.db_gateway.url,
                timeout_seconds=settings.db_gateway.timeout_seconds,
                signer=gateway_signer,
            )
            financial_effect_recovery = FinancialEffectRecovery(
                store=cast("RecoveryStore", recovery_store),
                bank=cast("RecoveryBank", central_bank_client),
                worker_id=None,
                max_delivery_attempts_per_cycle=(
                    settings.financial_effect_recovery.max_delivery_attempts_per_cycle
                ),
            )
            state.financial_effect_recovery = financial_effect_recovery
            financial_interval = settings.financial_effect_recovery.interval_seconds
            supervise_loop(
                state,
                "financial_effect_recovery_task",
                lambda: financial_effect_recovery.run_periodic(interval_seconds=financial_interval),
                restart_delay_seconds=financial_interval,
            )

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
            state.shutting_down = True
            await _stop_loop(state.financial_effect_recovery_task)
            if recovery_store is not None:
                recovery_store.close()
            await _stop_loop(state.posting_recovery_task)
            await _stop_loop(state.deadline_sweep_task)
            await _stop_loop(state.artifact_maintenance_task)
            if asset_manager is not None:
                asset_manager.close()
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
