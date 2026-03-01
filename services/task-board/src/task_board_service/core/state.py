"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.clients.identity_client import IdentityClient
    from task_board_service.clients.platform_signer import PlatformSigner
    from task_board_service.services.asset_manager import AssetManager
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
    asset_manager: AssetManager | None = None

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
