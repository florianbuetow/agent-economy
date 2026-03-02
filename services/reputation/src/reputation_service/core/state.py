"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from reputation_service.types import FeedbackRecord

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

    from reputation_service.services.identity_client import IdentityClient
    from reputation_service.services.protocol import FeedbackStorageInterface


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    feedback_store: FeedbackStorageInterface | None = None
    platform_agent: PlatformAgent | None = None
    identity_client: IdentityClient | None = None
    feedback_reveal_timeout_seconds: int = 0
    feedback_max_comment_length: int = 0

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat()


# Module-level mutable container to avoid `global` statement
_state_holder: dict[str, AppState] = {}

__all__ = ["AppState", "FeedbackRecord", "get_app_state", "init_app_state", "reset_app_state"]


def get_app_state() -> AppState:
    """Get the current application state."""
    state = _state_holder.get("current")
    if state is None:
        raise RuntimeError("Application state not initialized")
    return state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    state = AppState()
    _state_holder["current"] = state
    return state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    _state_holder.pop("current", None)
