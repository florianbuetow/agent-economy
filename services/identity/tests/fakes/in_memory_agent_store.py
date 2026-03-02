"""Compatibility agent storage used by legacy unit tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from typing import Any

from identity_service.services.errors import DuplicateAgentError


class InMemoryAgentStore:
    """Thread-safe in-memory agent storage with async methods."""

    def __init__(self, db_path: str) -> None:
        # Keep db_path argument for backward compatibility with existing tests.
        self._db_path = db_path
        self._lock = RLock()
        self._agents_by_id: dict[str, dict[str, str]] = {}
        self._agent_id_by_public_key: dict[str, str] = {}

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """Insert a new agent. Returns the stored agent record."""
        with self._lock:
            if public_key in self._agent_id_by_public_key:
                raise DuplicateAgentError(f"Public key already registered: {public_key}")

            agent_id = f"a-{uuid.uuid4()}"
            registered_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            record = {
                "agent_id": agent_id,
                "name": name,
                "public_key": public_key,
                "registered_at": registered_at,
            }
            self._agents_by_id[agent_id] = record
            self._agent_id_by_public_key[public_key] = agent_id
            return record

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """Look up a single agent by ID. Returns None if not found."""
        with self._lock:
            record = self._agents_by_id.get(agent_id)
            if record is None:
                return None
            return {
                "agent_id": record["agent_id"],
                "name": record["name"],
                "public_key": record["public_key"],
                "registered_at": record["registered_at"],
            }

    async def list_all(self) -> list[dict[str, str]]:
        """List all agents (without public keys), sorted by registration time."""
        with self._lock:
            records = sorted(self._agents_by_id.values(), key=lambda row: row["registered_at"])
            return [
                {
                    "agent_id": record["agent_id"],
                    "name": record["name"],
                    "registered_at": record["registered_at"],
                }
                for record in records
            ]

    async def count(self) -> int:
        """Count total registered agents."""
        with self._lock:
            return len(self._agents_by_id)

    async def close(self) -> None:
        """No-op close for API compatibility."""


def _run_sync[T](awaitable: Coroutine[Any, Any, T]) -> T:
    """Run async store operations from sync compatibility wrapper."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    msg = "AgentStore compatibility wrapper cannot run inside an active event loop"
    raise RuntimeError(msg)


class _SyncAgentStoreCompat:
    """Backward-compatible synchronous wrapper for existing tests/imports."""

    def __init__(self, db_path: str) -> None:
        self._store = InMemoryAgentStore(db_path=db_path)

    def insert(self, name: str, public_key: str) -> dict[str, str]:
        """Insert a new agent. Returns the agent record dict."""
        return _run_sync(self._store.insert(name=name, public_key=public_key))

    def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """Look up a single agent by ID. Returns None if not found."""
        return _run_sync(self._store.get_by_id(agent_id=agent_id))

    def list_all(self) -> list[dict[str, str]]:
        """List all agents (without public keys), sorted by registration time."""
        return _run_sync(self._store.list_all())

    def count(self) -> int:
        """Count total registered agents."""
        return _run_sync(self._store.count())

    def close(self) -> None:
        """No-op close to preserve previous API."""
        _run_sync(self._store.close())


# Backward compatibility alias for existing imports/tests.
AgentStore = _SyncAgentStoreCompat
