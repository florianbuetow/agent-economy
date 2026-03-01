# Phase 4 — HTTP Clients and Platform Signer

## Working Directory

All paths relative to `services/task-board/`.

---

## Directory Setup

Create the `src/task_board_service/clients/` directory. It will contain four files: the package init, an HTTP client for the Identity service, an HTTP client for the Central Bank, and a JWS token signer for platform-signed operations.

---

## File 1: `src/task_board_service/clients/__init__.py`

Create this file:

```python
"""HTTP clients for external service communication and platform signing."""

from task_board_service.clients.central_bank_client import CentralBankClient
from task_board_service.clients.identity_client import IdentityClient
from task_board_service.clients.platform_signer import PlatformSigner

__all__ = ["CentralBankClient", "IdentityClient", "PlatformSigner"]
```

---

## File 2: `src/task_board_service/clients/identity_client.py`

Create this file:

```python
"""Async HTTP client for the Identity service."""

from __future__ import annotations

from typing import Any

import httpx
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger


class IdentityClient:
    """
    Client for Identity service JWS verification.

    Delegates Ed25519 signature verification to the Identity service
    via POST /agents/verify-jws. The Task Board never touches private
    keys for incoming token verification — only the Identity service
    has access to the public key registry.
    """

    def __init__(
        self,
        base_url: str,
        verify_jws_path: str,
        timeout_seconds: int,
    ) -> None:
        self._base_url = base_url
        self._verify_jws_path = verify_jws_path
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS compact token via the Identity service.

        Args:
            token: JWS compact serialization string (header.payload.signature)

        Returns:
            dict with keys: valid (bool), agent_id (str), payload (dict)

        Raises:
            ServiceError: FORBIDDEN (403) if the Identity service says valid=false
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Identity service connection failed",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Cannot connect to Identity service",
                status_code=502,
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Identity service HTTP error",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Identity service request failed",
                status_code=502,
            ) from exc

        if response.status_code != 200:
            logger.warning(
                "Identity service unexpected status",
                extra={
                    "status_code": response.status_code,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Identity service returned unexpected status",
                status_code=502,
            )

        result: dict[str, Any] = response.json()

        if not result.get("valid", False):
            raise ServiceError(
                error="FORBIDDEN",
                message="JWS signature verification failed",
                status_code=403,
            )

        return result

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
```

**How it works:** Every authenticated Task Board endpoint extracts a JWS token from the request and calls `verify_jws()`. The Identity service decodes the JWS header, looks up the `kid` (agent ID), and verifies the Ed25519 signature against the stored public key. On success, the Identity service returns the decoded payload and the verified `agent_id`. On failure, it returns `valid: false`.

Error mapping:
- `valid: false` from Identity -> `403 FORBIDDEN` (signature invalid or agent not found)
- Connection refused, timeout, DNS failure -> `502 IDENTITY_SERVICE_UNAVAILABLE`
- Any non-200 status from Identity -> `502 IDENTITY_SERVICE_UNAVAILABLE`

---

## File 3: `src/task_board_service/clients/central_bank_client.py`

Create this file:

```python
"""Async HTTP client for the Central Bank service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger

if TYPE_CHECKING:
    from task_board_service.clients.platform_signer import PlatformSigner


class CentralBankClient:
    """
    Client for Central Bank escrow operations.

    Two operation types:
    1. lock_escrow — forwards the poster's pre-signed escrow token
       to POST /escrow/lock. The poster signs this token, not the platform.
    2. release_escrow — creates a platform-signed JWS token and calls
       POST /escrow/{escrow_id}/release. The platform signs this token
       because only the platform can authorize escrow releases.
    """

    def __init__(
        self,
        base_url: str,
        escrow_lock_path: str,
        escrow_release_path: str,
        timeout_seconds: int,
        platform_signer: PlatformSigner,
    ) -> None:
        self._base_url = base_url
        self._escrow_lock_path = escrow_lock_path
        self._escrow_release_path = escrow_release_path
        self._platform_signer = platform_signer
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def lock_escrow(self, escrow_token: str) -> dict[str, Any]:
        """
        Forward a poster-signed escrow lock token to the Central Bank.

        The Task Board does NOT verify this token — it only inspects the
        payload for cross-validation (task_id, amount). The Central Bank
        performs full JWS verification via the Identity service.

        Args:
            escrow_token: JWS compact token signed by the poster with
                         action "escrow_lock"

        Returns:
            dict with keys: escrow_id, amount, task_id, status

        Raises:
            ServiceError: INSUFFICIENT_FUNDS (402) if the poster cannot cover the reward
            ServiceError: CENTRAL_BANK_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        try:
            response = await self._client.post(
                self._escrow_lock_path,
                json={"token": escrow_token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Central Bank connection failed",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Cannot connect to Central Bank",
                status_code=502,
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Central Bank HTTP error",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Central Bank request failed",
                status_code=502,
            ) from exc

        if response.status_code == 201:
            result: dict[str, Any] = response.json()
            return result

        if response.status_code == 402:
            error_body: dict[str, Any] = response.json()
            raise ServiceError(
                error="INSUFFICIENT_FUNDS",
                message="Poster has insufficient funds to cover the task reward",
                status_code=402,
                details=error_body,
            )

        logger.warning(
            "Central Bank unexpected status on escrow lock",
            extra={
                "status_code": response.status_code,
                "base_url": self._base_url,
            },
        )
        raise ServiceError(
            error="CENTRAL_BANK_UNAVAILABLE",
            message="Central Bank returned unexpected status",
            status_code=502,
        )

    async def release_escrow(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, Any]:
        """
        Release escrow funds to a recipient via a platform-signed token.

        Used for:
        - Cancellation: release to poster
        - Approval (explicit or auto): release to worker
        - Expiration: release to poster

        Args:
            escrow_id: The Central Bank escrow identifier (esc-<uuid4>)
            recipient_account_id: The agent_id of the recipient

        Returns:
            dict with escrow release confirmation

        Raises:
            ServiceError: CENTRAL_BANK_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        # Sign a platform JWS token for the escrow release
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            }
        )

        # Build the release URL from the template
        release_path = self._escrow_release_path.format(escrow_id=escrow_id)

        try:
            response = await self._client.post(
                release_path,
                json={"token": signed_token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Central Bank connection failed on escrow release",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Cannot connect to Central Bank for escrow release",
                status_code=502,
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Central Bank HTTP error on escrow release",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Central Bank escrow release request failed",
                status_code=502,
            ) from exc

        if response.status_code == 200:
            result: dict[str, Any] = response.json()
            return result

        logger.warning(
            "Central Bank unexpected status on escrow release",
            extra={
                "status_code": response.status_code,
                "escrow_id": escrow_id,
                "base_url": self._base_url,
            },
        )
        raise ServiceError(
            error="CENTRAL_BANK_UNAVAILABLE",
            message="Central Bank returned unexpected status on escrow release",
            status_code=502,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
```

**Key design decisions:**

1. **`lock_escrow` forwards the poster's token verbatim.** The Task Board does not sign escrow locks — the poster pre-signs the token. The Task Board only inspects the token's payload (by base64url-decoding, without signature verification) for cross-validation. Full cryptographic verification happens at the Central Bank.

2. **`release_escrow` creates a platform-signed token.** Only the platform agent can authorize escrow releases. The `PlatformSigner` creates a JWS compact token signed with the platform's Ed25519 private key, and this client sends it to the Central Bank.

3. **402 from Central Bank maps to 402 INSUFFICIENT_FUNDS.** This is the one case where the Central Bank's error is semantically propagated rather than collapsed into 502. All other non-success statuses map to 502.

4. **The `escrow_release_path` is a template string** (`/escrow/{escrow_id}/release`). The `format()` call substitutes the actual escrow ID at call time.

---

## File 4: `src/task_board_service/clients/platform_signer.py`

Create this file:

```python
"""Platform JWS token signer for outgoing escrow operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from joserfc import jws
from joserfc.jwk import OKPKey


class PlatformSigner:
    """
    Creates JWS compact tokens signed with the platform agent's Ed25519 private key.

    Used for outgoing calls to the Central Bank where the Task Board acts as
    the platform agent (escrow release on approval, cancellation, expiration).

    The platform agent must be registered with the Identity service, and the
    corresponding public key must be stored there. The Central Bank verifies
    the platform-signed token via the Identity service.
    """

    def __init__(self, agent_id: str, private_key_path: str) -> None:
        self._agent_id = agent_id

        # Load Ed25519 private key from PEM file
        pem_data = Path(private_key_path).read_bytes()
        private_key = load_pem_private_key(pem_data, password=None)

        # Extract raw key bytes for JWK construction
        raw_private = private_key.private_bytes_raw()  # type: ignore[union-attr]
        raw_public = private_key.public_key().public_bytes_raw()  # type: ignore[union-attr]

        # Build OKP JWK for joserfc
        import base64

        jwk_dict = {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
        }
        self._key = OKPKey.import_key(jwk_dict)

    def sign(self, payload: dict[str, Any]) -> str:
        """
        Create a JWS compact serialization token.

        Args:
            payload: The JWS payload as a dict. Must include an "action" field
                    (e.g., "escrow_release").

        Returns:
            JWS compact serialization string (header.payload.signature)
        """
        protected = {"alg": "EdDSA", "kid": self._agent_id}
        payload_bytes = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode()
        return jws.serialize_compact(protected, payload_bytes, self._key, algorithms=["EdDSA"])
```

**How it works:**

1. At startup, the `PlatformSigner` loads the Ed25519 private key from a PEM file specified in `config.yaml` (`platform.private_key_path`). If the file is missing or the key is invalid, the constructor raises and the service fails to start — fail fast.

2. The private key is converted to a JWK `OKPKey` object that `joserfc` can use for signing. This is the same JWK format used throughout the codebase (see `tests/unit/routers/conftest.py` in the central-bank service and `tests/unit/routers/test_verify_jws.py` in the identity service).

3. The `sign()` method creates a JWS compact token with:
   - Header: `{"alg": "EdDSA", "kid": "<platform_agent_id>"}`
   - Payload: JSON-serialized with compact separators and sorted keys (deterministic output)
   - Signature: Ed25519 signature over `base64url(header).base64url(payload)`

4. The `algorithms=["EdDSA"]` parameter is required by joserfc to explicitly permit the algorithm. This matches the pattern used in all existing test helpers.

**Why compact JSON with sorted keys?** The JWS signature covers the exact payload bytes. Using `separators=(",", ":")` and `sort_keys=True` produces deterministic JSON regardless of dict insertion order. This is the same convention used by the Identity service's JWS verification (see `agent_registry.py`) and all test helpers.

---

## Verification

```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.

**Note:** This phase depends on:
- Phase 2 (`task_board_service.logging` for the `get_logger` import in clients)
- Phase 3 (`service_commons.exceptions.ServiceError` used by both clients)
- Phase 1 (`cryptography`, `httpx`, and `joserfc` dependencies installed)

The ruff checks will pass regardless of import resolution order (ruff is a static linter, not an import checker). But running the service requires all phases to be complete through Phase 7.
