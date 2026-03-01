"""Identity service mixin — agent registration and lookup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    import httpx

    from base_agent.config import AgentConfig


class _IdentityClient(Protocol):
    config: AgentConfig
    name: str
    agent_id: str | None

    def get_public_key_b64(self) -> str: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...

    async def _request_raw(self, method: str, url: str, **kwargs: Any) -> httpx.Response: ...

    async def get_agent_info(self, agent_id: str) -> dict[str, Any]: ...

    async def list_agents(self) -> list[dict[str, Any]]: ...


class IdentityMixin:
    """Methods for interacting with the Identity service (port 8001)."""

    async def register(self: _IdentityClient) -> dict[str, Any]:
        """Register this agent with the Identity service."""
        url = f"{self.config.identity_url}/agents/register"
        payload = {
            "name": self.name,
            "public_key": f"ed25519:{self.get_public_key_b64()}",
        }

        response = await self._request_raw("POST", url, json=payload)

        if response.status_code == 201:
            registration = cast("dict[str, Any]", response.json())
            self.agent_id = registration["agent_id"]
            return registration

        if response.status_code == 409:
            agents = await self.list_agents()
            my_public_key = f"ed25519:{self.get_public_key_b64()}"
            existing_agent_id: str | None = None
            for agent in agents:
                candidate_id = agent.get("agent_id")
                if isinstance(candidate_id, str):
                    full = await self.get_agent_info(candidate_id)
                    if full.get("public_key") == my_public_key:
                        existing_agent_id = candidate_id
                        break
            if existing_agent_id is None:
                msg = "Could not find existing agent after 409 conflict"
                raise RuntimeError(msg)
            full_record = await self.get_agent_info(existing_agent_id)
            self.agent_id = existing_agent_id
            return full_record

        response.raise_for_status()
        msg = f"Unexpected registration response status: {response.status_code}"
        raise RuntimeError(msg)

    async def get_agent_info(self: _IdentityClient, agent_id: str) -> dict[str, Any]:
        """Get a single agent record from Identity."""
        url = f"{self.config.identity_url}/agents/{agent_id}"
        return await self._request("GET", url)

    async def list_agents(self: _IdentityClient) -> list[dict[str, Any]]:
        """List registered agents."""
        url = f"{self.config.identity_url}/agents"
        response = await self._request("GET", url)
        return cast("list[dict[str, Any]]", response["agents"])

    async def verify_jws(self: _IdentityClient, token: str) -> dict[str, Any]:
        """Verify a compact JWS token via Identity."""
        url = f"{self.config.identity_url}/agents/verify-jws"
        return await self._request("POST", url, json={"token": token})
