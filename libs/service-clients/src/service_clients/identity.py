"""Identity service client."""

from __future__ import annotations

from typing import Any

from service_clients.base import BaseServiceClient


class IdentityClient(BaseServiceClient):
    """Async HTTP client for Identity service endpoints."""

    def __init__(
        self,
        base_url: str,
        get_agent_path: str,
        verify_jws_path: str,
        timeout_seconds: int,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="identity_service",
        )
        self._get_agent_path = get_agent_path
        self._verify_jws_path = verify_jws_path

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Look up an agent by id."""
        response = await self._get(
            f"{self._get_agent_path}/{agent_id}",
            expected_status=200,
            not_found_returns_none=True,
        )
        if response is None:
            return None
        return response

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """Verify a JWS token via Identity."""
        return await self._post(
            self._verify_jws_path,
            payload={"token": token},
            expected_status=200,
        )
