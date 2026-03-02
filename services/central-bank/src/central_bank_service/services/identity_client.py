"""HTTP client for the Identity service."""

from __future__ import annotations

from typing import Any, cast

import httpx
from service_commons.exceptions import ServiceError


class IdentityClient:
    """
    Async HTTP client for the Identity service.

    Handles agent lookup by delegating to the Identity service's API.
    """

    def __init__(
        self,
        base_url: str,
        get_agent_path: str,
    ) -> None:
        self._base_url = base_url
        self._get_agent_path = get_agent_path
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """
        Look up an agent by ID via the Identity service.

        Returns the agent record or None if not found.

        Raises:
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE if Identity is unreachable.
        """
        try:
            response = await self._client.get(
                f"{self._get_agent_path}/{agent_id}",
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            return cast("dict[str, Any]", response.json())
        if response.status_code == 404:
            return None

        raise ServiceError(
            "IDENTITY_SERVICE_ERROR",
            f"Identity service returned {response.status_code}",
            502,
            {},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
