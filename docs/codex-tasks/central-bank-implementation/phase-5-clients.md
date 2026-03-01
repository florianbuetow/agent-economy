# Phase 5 — Identity Client

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B5: Implement IdentityClient (HTTP client for Identity service)

### Step 5.1: Write identity_client.py

Create `services/central-bank/src/central_bank_service/services/identity_client.py`:

```python
"""HTTP client for the Identity service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    pass


class IdentityClient:
    """
    Async HTTP client for the Identity service.

    Handles JWS token verification and agent lookup by delegating
    to the Identity service's API.
    """

    def __init__(
        self,
        base_url: str,
        verify_jws_path: str,
        get_agent_path: str,
    ) -> None:
        self._base_url = base_url
        self._verify_jws_path = verify_jws_path
        self._get_agent_path = get_agent_path
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service.

        Returns the full response body from Identity service:
        {"valid": True, "agent_id": "...", "payload": {...}}
        or {"valid": False, "reason": "..."}

        Raises:
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE if Identity is unreachable.
            ServiceError: INVALID_JWS if Identity returns 400.
            ServiceError: AGENT_NOT_FOUND if Identity returns 404.
        """
        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            return response.json()

        # Propagate Identity service errors
        try:
            error_body = response.json()
            error_code = error_body.get("error", "UNKNOWN_ERROR")
            error_message = error_body.get("message", "Unknown error from Identity service")
        except Exception:
            error_code = "IDENTITY_SERVICE_ERROR"
            error_message = f"Identity service returned {response.status_code}"

        raise ServiceError(error_code, error_message, response.status_code, {})

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
            return response.json()
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
```

### Step 5.2: Commit

```bash
git add services/central-bank/src/central_bank_service/services/identity_client.py
git commit -m "feat(central-bank): add IdentityClient for Identity service communication"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
