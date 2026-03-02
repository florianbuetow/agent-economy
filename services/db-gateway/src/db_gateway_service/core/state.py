"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db_gateway_service.services.db_reader import DbReader
    from db_gateway_service.services.db_writer import DbWriter


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    db_writer: DbWriter | None = None
    db_reader: DbReader | None = None

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")


# Module-level mutable container avoids 'global' statement (PLW0603)
_state_holder: list[AppState | None] = [None]


def get_app_state() -> AppState:
    """Get the current application state."""
    state = _state_holder[0]
    if state is None:
        msg = "Application state not initialized"
        raise RuntimeError(msg)
    return state


def init_app_state() -> AppState:
    """Initialize application state. Called during startup."""
    state = AppState()
    _state_holder[0] = state
    return state


def reset_app_state() -> None:
    """Reset application state. Used in testing."""
    _state_holder[0] = None
