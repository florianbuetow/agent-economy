"""DB Gateway-backed agent storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from service_clients.gateway import GatewayClient
from service_commons.exceptions import ServiceError

from identity_service.logging import get_logger
from identity_service.services.errors import DuplicateAgentError

logger = get_logger(__name__)


class AgentDbClient:
    """Agent storage backed by the DB Gateway HTTP API (via the shared GatewayClient).

    GatewayClient raises ServiceError for every non-2xx gateway response; this
    facade translates that into AgentDbClient's own pre-consolidation contract
    (DuplicateAgentError on a 409, RuntimeError("Gateway error: ...") on anything
    else unexpected), so callers built against this class see no behavior change.
    """

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._gateway = GatewayClient(base_url=base_url, timeout_seconds=timeout_seconds)

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """
        Insert a new agent via the DB Gateway.

        Create a new agent with event metadata.
        Returns dict with keys: agent_id, name, public_key, registered_at.
        Raises DuplicateAgentError if public_key already exists.
        """
        agent_id = self._new_agent_id()
        registered_at = self._now()

        try:
            result = await self._gateway.register_agent(
                agent_id=agent_id,
                name=name,
                public_key=public_key,
                registered_at=registered_at,
            )
        except ServiceError as exc:
            if exc.status_code == 409:
                raise DuplicateAgentError(exc.message) from exc
            raise self._as_gateway_runtime_error(exc) from exc

        return {
            "agent_id": str(result.get("agent_id", agent_id)),
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
        try:
            data = await self._gateway.get_agent(agent_id)
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc
        if data is None:
            return None
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
        try:
            agents = await self._gateway.list_agents()
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc
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
        try:
            return await self._gateway.count_agents()
        except ServiceError as exc:
            raise self._as_gateway_runtime_error(exc) from exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._gateway.close()

    @property
    def _client(self) -> object:
        """Backward-compatible accessor: the underlying httpx.AsyncClient, via
        GatewayClient's public `connection` property (never a private reach-in).
        """
        return self._gateway.connection

    @staticmethod
    def _new_agent_id() -> str:
        return f"a-{uuid.uuid4()}"

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _as_gateway_runtime_error(exc: ServiceError) -> RuntimeError:
        return RuntimeError(f"Gateway error: {exc.status_code} {exc.message}")


__all__ = ["AgentDbClient"]
