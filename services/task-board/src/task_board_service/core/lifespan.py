"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from task_board_service.clients.central_bank_client import CentralBankClient
from task_board_service.clients.identity_client import IdentityClient
from task_board_service.clients.platform_signer import PlatformSigner
from task_board_service.config import get_settings
from task_board_service.core.state import init_app_state
from task_board_service.logging import get_logger, setup_logging
from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_manager import TaskManager
from task_board_service.services.task_store import TaskStore
from task_board_service.services.token_validator import TokenValidator

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

    # Resolve platform private key path (use configured path or fallback in data dir)
    private_key_path = settings.platform.private_key_path
    if not private_key_path:
        private_key_path = str(db_directory / "platform.pem")

    private_key_file = Path(private_key_path)
    if not private_key_file.exists():
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        private_key_file.parent.mkdir(parents=True, exist_ok=True)
        private_key_file.write_bytes(pem)

    # Initialize PlatformSigner (loads Ed25519 private key from disk)
    platform_signer = PlatformSigner(
        private_key_path=private_key_path,
        platform_agent_id=settings.platform.agent_id,
    )
    state.platform_signer = platform_signer

    # Initialize IdentityClient (HTTP client for JWS verification)
    identity_client = IdentityClient(
        base_url=settings.identity.base_url,
        verify_jws_path=settings.identity.verify_jws_path,
        timeout_seconds=settings.identity.timeout_seconds,
    )
    state.identity_client = identity_client

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
    store = TaskStore(db_path=db_path)
    escrow_coordinator = EscrowCoordinator(central_bank_client=central_bank_client, store=store)
    state.escrow_coordinator = escrow_coordinator
    token_validator = TokenValidator(identity_client=identity_client)
    state.token_validator = token_validator
    task_manager = TaskManager(
        store=store,
        identity_client=identity_client,
        central_bank_client=central_bank_client,
        escrow_coordinator=escrow_coordinator,
        token_validator=token_validator,
        platform_signer=platform_signer,
        asset_storage_path=asset_storage_path,
        max_file_size=max_file_size,
        max_files_per_task=max_files_per_task,
        platform_agent_id=settings.platform.agent_id,
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
            "identity_base_url": settings.identity.base_url,
            "central_bank_base_url": settings.central_bank.base_url,
            "platform_agent_id": settings.platform.agent_id,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})

    # Close task manager (closes SQLite database)
    task_manager.close()

    # Close HTTP clients (closes httpx async clients)
    await identity_client.close()
    await central_bank_client.close()
