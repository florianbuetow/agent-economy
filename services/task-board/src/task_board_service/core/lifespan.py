"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

from base_agent.factory import AgentFactory
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from service_commons.config import load_yaml_config

from task_board_service.clients.central_bank_client import CentralBankClient
from task_board_service.clients.platform_signer import PlatformSigner
from task_board_service.config import get_config_path, get_settings
from task_board_service.core.state import init_app_state
from task_board_service.logging import get_logger, setup_logging
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.identity_client import IdentityClient
from task_board_service.services.task_db_client import TaskDbClient
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.token_validator import TokenValidator

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from base_agent.platform import PlatformAgent
    from fastapi import FastAPI

    from task_board_service.services.protocol import TaskStorageInterface


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    db_path = settings.database.path
    db_directory = Path(db_path).parent

    # Resolve asset settings from explicit assets section or legacy limits section
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

    # Resolve platform key material and platform agent identity.
    private_key_path = settings.platform.private_key_path
    platform_agent_id = settings.platform.agent_id

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
        platform_agent_id = platform_agent.agent_id

        agent_config = load_yaml_config(config_path)
        data_config = agent_config.get("data")
        if not isinstance(data_config, dict):
            msg = "Invalid agent config: missing data section"
            raise RuntimeError(msg)

        keys_dir = data_config.get("keys_dir")
        if not isinstance(keys_dir, str):
            msg = "Invalid agent config: data.keys_dir must be a string"
            raise RuntimeError(msg)

        keys_dir_path = Path(keys_dir)
        if not keys_dir_path.is_absolute():
            keys_dir_path = config_path.parent / keys_dir_path
        private_key_path = str((keys_dir_path / "platform.key").resolve())

        logger.info("Platform agent registered", extra={"agent_id": platform_agent_id})
    else:
        if not private_key_path:
            private_key_path = str(db_directory / "platform.pem")

        private_key_file = Path(private_key_path)
        if not private_key_file.exists():
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
            private_key_file.parent.mkdir(parents=True, exist_ok=True)
            private_key_file.write_bytes(pem)

    # Initialize IdentityClient (HTTP client for JWS verification)
    identity_client: IdentityClient | None = None
    if settings.identity is not None:
        identity_client = IdentityClient(
            base_url=settings.identity.base_url,
            verify_jws_path=settings.identity.verify_jws_path,
        )
        state.identity_client = identity_client

    # Initialize PlatformSigner (loads Ed25519 private key from disk)
    platform_signer = PlatformSigner(
        private_key_path=private_key_path,
        platform_agent_id=platform_agent_id,
    )
    state.platform_signer = platform_signer

    # Initialize CentralBankClient (HTTP client for escrow operations)
    central_bank_client = CentralBankClient(
        base_url=settings.central_bank.base_url,
        escrow_lock_path=settings.central_bank.escrow_lock_path,
        escrow_release_path=settings.central_bank.escrow_release_path,
        escrow_split_path=settings.central_bank.escrow_split_path,
        timeout_seconds=settings.central_bank.timeout_seconds,
        platform_signer=platform_signer,
    )
    state.central_bank_client = central_bank_client

    # Initialize TaskManager (all business logic)
    store: TaskStorageInterface
    if settings.db_gateway is None:
        msg = "db_gateway configuration is required"
        raise RuntimeError(msg)

    store = TaskDbClient(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )
    state.store = store
    escrow_coordinator = EscrowCoordinator(central_bank_client=central_bank_client, store=store)
    state.escrow_coordinator = escrow_coordinator
    token_validator = TokenValidator(
        platform_agent=cast("PlatformAgent", state.platform_agent),
        identity_client=identity_client,
    )
    state.token_validator = token_validator
    deadline_evaluator = DeadlineEvaluator(store=store, escrow_coordinator=escrow_coordinator)
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
        platform_signer=platform_signer,
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

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    # Close task manager (closes SQLite database)
    task_manager.close()

    if state.platform_agent is not None:
        await state.platform_agent.close()

    # Close HTTP clients (closes httpx async clients)
    await central_bank_client.close()
    if identity_client is not None:
        await identity_client.close()
