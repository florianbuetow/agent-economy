"""Storage interface protocol for the Identity service."""

from __future__ import annotations

from typing import Protocol


class IdentityStorageInterface(Protocol):
    """
    Async storage interface for agent identity data.

    Implementations may use local SQLite, HTTP gateway, or any other backend.
    """

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """
        Insert a new agent.

        Returns dict with keys: agent_id, name, public_key, registered_at.
        Raises DuplicateAgentError if public_key already exists.
        """
        ...

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """Look up a single agent by ID. Returns None if not found."""
        ...

    async def list_all(self) -> list[dict[str, str]]:
        """List all agents (without public keys). Sorted by registered_at."""
        ...

    async def count(self) -> int:
        """Count total registered agents."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...
