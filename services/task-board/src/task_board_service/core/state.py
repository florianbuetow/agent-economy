"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.clients.identity_client import IdentityClient
    from task_board_service.clients.platform_signer import PlatformSigner
    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.task_manager import TaskManager
    from task_board_service.services.token_validator import TokenValidator


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_manager: TaskManager | None = None
    identity_client: IdentityClient | None = None
    central_bank_client: CentralBankClient | None = None
    platform_signer: PlatformSigner | None = None
    escrow_coordinator: EscrowCoordinator | None = None
    token_validator: TokenValidator | None = None

    def __setattr__(self, name: str, value: Any) -> None:
        """Keep TaskManager dependency references in sync with AppState fields."""
        super().__setattr__(name, value)

        escrow_coordinator = self.__dict__.get("escrow_coordinator")
        if name == "central_bank_client" and value is not None and escrow_coordinator is not None:
            escrow_coordinator._central_bank_client = value

        token_validator = self.__dict__.get("token_validator")
        if name == "identity_client" and value is not None and token_validator is not None:
            token_validator._identity_client = value

        task_manager = self.__dict__.get("task_manager")
        if task_manager is None:
            return

        if name == "identity_client" and value is not None:
            task_manager.set_identity_client(value)
        elif name == "central_bank_client" and value is not None:
            task_manager.set_central_bank_client(value)
        elif name == "platform_signer" and value is not None:
            task_manager.set_platform_signer(value)
        elif name == "task_manager" and value is not None:
            identity_client = self.__dict__.get("identity_client")
            central_bank_client = self.__dict__.get("central_bank_client")
            platform_signer = self.__dict__.get("platform_signer")
            if identity_client is not None:
                value.set_identity_client(identity_client)
            if central_bank_client is not None:
                value.set_central_bank_client(central_bank_client)
            if platform_signer is not None:
                value.set_platform_signer(platform_signer)

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Global application state container
_state_container: dict[str, AppState | None] = {"app_state": None}


def get_app_state() -> AppState:
    """Get the current application state."""
    app_state = _state_container["app_state"]
    if app_state is None:
        msg = "Application state not initialized"
        raise RuntimeError(msg)
    return app_state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    app_state = AppState()
    _state_container["app_state"] = app_state
    return app_state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    _state_container["app_state"] = None
