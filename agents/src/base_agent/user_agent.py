"""UserAgent — UI-driven agent for human task lifecycle operations."""

from __future__ import annotations

from base_agent.platform import PlatformAgent


class UserAgent(PlatformAgent):
    """Agent used by the UI service for human-driven task lifecycle operations.

    Inherits PlatformAgent's keys and identity. All operations appear as
    the platform agent. Provides the same task-board, bank, and reputation
    methods as any other agent.
    """

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"UserAgent(name={self.name!r}{registered})"
