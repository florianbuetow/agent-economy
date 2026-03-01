"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from court_service.services.central_bank_client import CentralBankClient
    from court_service.services.dispute_service import DisputeService
    from court_service.services.identity_client import IdentityClient
    from court_service.services.platform_signer import PlatformSigner
    from court_service.services.reputation_client import ReputationClient
    from court_service.services.task_board_client import TaskBoardClient


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    dispute_service: DisputeService | None = None
    identity_client: IdentityClient | None = None
    platform_signer: PlatformSigner | None = None
    task_board_client: TaskBoardClient | None = None
    central_bank_client: CentralBankClient | None = None
    reputation_client: ReputationClient | None = None
    judges: list[object] | None = None

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


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
