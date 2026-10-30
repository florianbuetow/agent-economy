"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import asyncio

    from service_auth.platform import PlatformAgent
    from service_clients.bank import BankClient, TokenSigner
    from service_clients.identity import IdentityClient

    from task_board_service.services.asset_manager import AssetManager
    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
    from task_board_service.services.posting_recovery import PostingRecovery
    from task_board_service.services.protocol import TaskStorageInterface
    from task_board_service.services.task_manager import TaskManager
    from task_board_service.services.token_validator import TokenValidator

LoopStatus = Literal["running", "dead"]


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_manager: TaskManager | None = None
    central_bank_client: BankClient | None = None
    platform_signer: TokenSigner | None = None
    platform_agent: PlatformAgent | None = None
    identity_client: IdentityClient | None = None
    escrow_coordinator: EscrowCoordinator | None = None
    token_validator: TokenValidator | None = None
    asset_manager: AssetManager | None = None
    store: TaskStorageInterface | None = None
    deadline_sweep_task: asyncio.Task[None] | None = None
    posting_recovery: PostingRecovery | None = None
    posting_recovery_task: asyncio.Task[None] | None = None
    financial_effect_recovery: FinancialEffectRecovery | None = None
    financial_effect_recovery_task: asyncio.Task[None] | None = None
    artifact_maintenance_task: asyncio.Task[None] | None = None
    shutting_down: bool = False

    def background_loops(self) -> dict[str, LoopStatus]:
        """Liveness of each supervised background loop: "running" or "dead".

        The artifact maintenance loop only exists when the `artifacts` config section
        is present, so it is reported only once it has been started.
        """
        loops = {
            "deadline_sweep": self.deadline_sweep_task,
            "posting_recovery": self.posting_recovery_task,
            "financial_effect_recovery": self.financial_effect_recovery_task,
        }
        if self.artifact_maintenance_task is not None:
            loops["artifact_maintenance"] = self.artifact_maintenance_task
        return {
            name: "running" if task is not None and not task.done() else "dead"
            for name, task in loops.items()
        }

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
