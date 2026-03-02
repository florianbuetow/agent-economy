"""DB Gateway-backed agent storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from identity_service.logging import get_logger
from identity_service.services.errors import DuplicateAgentError

logger = get_logger(__name__)


class AgentDbClient:
    """Agent storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._agents_path = "/identity" + "/agents"
        self._agents_count_path = self._agents_path + "/count"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """
        Insert a new agent via the DB Gateway.

        Create a new agent with event metadata.
        Returns dict with keys: agent_id, name, public_key, registered_at.
        Raises DuplicateAgentError if public_key already exists.
        """
        agent_id = f"a-{uuid.uuid4()}"
        registered_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "agent_id": agent_id,
                "summary": f"{name} registered as agent",
                "payload": json.dumps({"agent_name": name}),
            },
        }

        response = await self._client.post(self._agents_path, json=payload)

        if response.status_code == 409:
            data = response.json()
            error_msg = data.get("message", "Public key already registered")
            raise DuplicateAgentError(error_msg)

        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        resp_data = response.json()
        return {
            "agent_id": resp_data.get("agent_id", agent_id),
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
        }

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """
        Look up a single agent by ID via the DB Gateway.

        Query the agent collection by id.
        Returns the full agent record or None if not found.
        """
        response = await self._client.get(f"{self._agents_path}/{agent_id}")

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        return {
            "agent_id": str(data["agent_id"]),
            "name": str(data["name"]),
            "public_key": str(data["public_key"]),
            "registered_at": str(data["registered_at"]),
        }

    async def list_all(self) -> list[dict[str, str]]:
        """
        List all agents via the DB Gateway.

        Query all registered agents.
        Returns list of agent summaries sorted by registration time.
        Public keys are omitted for brevity.
        """
        response = await self._client.get(self._agents_path)

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        agents: list[dict[str, Any]] = data["agents"]
        return [
            {
                "agent_id": str(agent["agent_id"]),
                "name": str(agent["name"]),
                "registered_at": str(agent["registered_at"]),
            }
            for agent in agents
        ]

    async def count(self) -> int:
        """
        Count total registered agents via the DB Gateway.

        Query the aggregate agent count.
        Returns integer count.
        """
        response = await self._client.get(self._agents_count_path)

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        return int(data["count"])

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


__all__ = ["AgentDbClient"]
