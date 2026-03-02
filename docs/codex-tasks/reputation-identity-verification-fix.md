# Fix: Restore Identity Service JWS Verification in Reputation Service

## Context

The reputation service currently verifies JWS tokens using the local platform agent's public key (`platform_agent.validate_certificate()`). This only works for tokens signed by the platform agent itself. Regular agents signing feedback submissions with their own Ed25519 keys always get 403 because the platform key doesn't match their key.

The fix: route JWS verification through the Identity service's `POST /agents/verify-jws` endpoint, which looks up each agent's registered public key and verifies the signature correctly.

This same fix has already been applied to the central-bank and task-board services. The reputation service needs the identical pattern.

## Affected e2e Tests

These 4 e2e tests currently fail with 403 Forbidden:
- `test_mutual_feedback_exchange`
- `test_sealed_feedback_invisible_until_mutual`
- `test_self_feedback_rejected`
- `test_duplicate_feedback_rejected`

## Implementation Steps

### Step 1: Add IdentityConfig to config.py

**File:** `services/reputation/src/reputation_service/config.py`

Add a new Pydantic model class `IdentityConfig` and add it as an optional field on `Settings`.

```python
class IdentityConfig(BaseModel):
    """Identity service configuration."""

    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
```

Add to `Settings` class:
```python
identity: IdentityConfig | None = None
```

**IMPORTANT:** The field MUST be `identity: IdentityConfig | None = None` (optional with None default). This is required because existing unit tests have config YAML that does NOT include an `identity` section, and the config model uses `extra="forbid"`. Making it optional avoids breaking existing tests. Add the `# nosemgrep` comment to suppress the no-default-parameter-values rule if needed on the Settings class — but note that Settings is not a regular class, it's a Pydantic BaseModel, so semgrep may not flag it.

### Step 2: Add identity section to config.yaml

**File:** `services/reputation/config.yaml`

Add after the `platform:` section:

```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
```

### Step 3: Create IdentityClient

**File:** `services/reputation/src/reputation_service/services/identity_client.py` (NEW FILE)

Create this file with the exact same pattern used in the task-board service. Copy the pattern from `services/task-board/src/task_board_service/services/identity_client.py`.

The class must:
- Accept `base_url: str` and `verify_jws_path: str` in `__init__` (NO default values)
- Create an `httpx.AsyncClient` with `timeout=10.0`
- Have `async def verify_jws(self, token: str) -> dict[str, Any]` that POSTs to the verify-jws endpoint
- Have `async def close(self) -> None` that calls `aclose()` on the httpx client
- Use `service_commons.exceptions.ServiceError` (NOT `reputation_service.core.exceptions.ServiceError`)
- Error codes: `"identity_service_unavailable"` for HTTP errors, `"identity_service_error"` for non-200 responses

Here is the exact implementation to use:

```python
"""HTTP client for the Identity service."""

from __future__ import annotations

from typing import Any, cast

import httpx
from service_commons.exceptions import ServiceError


class IdentityClient:
    """
    Async HTTP client for the Identity service.

    Handles JWS verification by delegating to the Identity service's API.
    """

    def __init__(
        self,
        base_url: str,
        verify_jws_path: str,
    ) -> None:
        self._base_url = base_url
        self._verify_jws_path = verify_jws_path
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service.

        Returns the verification result dict containing valid, agent_id, and payload.

        Raises:
            ServiceError: identity_service_unavailable if Identity is unreachable.
            ServiceError: On non-200 responses, propagates the error from the Identity service.
        """
        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            return cast("dict[str, Any]", response.json())

        try:
            error_body = response.json()
            error_code = error_body.get("error", "identity_service_error")
            error_message = error_body.get(
                "message",
                f"Identity service returned {response.status_code}",
            )
        except Exception:
            error_code = "identity_service_error"
            error_message = (
                f"Identity service returned {response.status_code}"
            )

        raise ServiceError(
            error_code,
            error_message,
            response.status_code,
            {},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
```

### Step 4: Add identity_client to AppState

**File:** `services/reputation/src/reputation_service/core/state.py`

Add import (under `TYPE_CHECKING`):
```python
from reputation_service.services.identity_client import IdentityClient
```

Add field to the `AppState` dataclass:
```python
identity_client: IdentityClient | None = None
```

### Step 5: Initialize IdentityClient in lifespan

**File:** `services/reputation/src/reputation_service/core/lifespan.py`

Add import at top:
```python
from reputation_service.services.identity_client import IdentityClient
```

In the startup section, AFTER the platform agent initialization block and BEFORE the logger.info "Service starting" block, add:

```python
    # Initialize identity client for JWS verification
    if settings.identity is not None:
        state.identity_client = IdentityClient(
            base_url=settings.identity.base_url,
            verify_jws_path=settings.identity.verify_jws_path,
        )
```

In the shutdown section, BEFORE the feedback_store close, add:

```python
    if state.identity_client is not None:
        await state.identity_client.close()
```

### Step 6: Update feedback.py to use Identity service verification

**File:** `services/reputation/src/reputation_service/routers/feedback.py`

This is the core change. In `submit_feedback_endpoint()`, replace the local platform-agent verification block (lines 144-186) with Identity service verification.

**Remove these imports** (no longer needed):
- `import base64` (line 5)
- `from cryptography.exceptions import InvalidSignature` (line 8)

**Remove the `_extract_signer_agent_id` function** entirely (lines 66-94). The agent_id will now come from the Identity service response, not from parsing the JWS header locally.

**Replace the verification block** in `submit_feedback_endpoint()`. The current code (lines 144-186):

```python
    # --- Local JWS verification via platform agent ---
    state = get_app_state()
    if state.platform_agent is None:
        raise ServiceError(...)
    ...
    payload_raw = state.platform_agent.validate_certificate(token)
    ...
    payload: dict[str, object] = payload_raw
    signer_agent_id: str = _extract_signer_agent_id(token)
```

Replace with:

```python
    # --- JWS verification via Identity service ---
    state = get_app_state()
    if state.identity_client is None:
        raise ServiceError(
            error="service_not_ready",
            message="Identity client not initialized",
            status_code=503,
            details={},
        )
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )

    verify_result = await state.identity_client.verify_jws(token)

    if not verify_result.get("valid"):
        raise ServiceError(
            error="forbidden",
            message="JWS signature verification failed",
            status_code=403,
            details={},
        )

    payload_from_identity = verify_result.get("payload")
    if not isinstance(payload_from_identity, dict):
        raise ServiceError(
            error="invalid_payload",
            message="JWS payload must be a JSON object",
            status_code=400,
            details={},
        )

    payload: dict[str, object] = payload_from_identity
    signer_agent_id: str = verify_result.get("agent_id", "")
    if not signer_agent_id:
        raise ServiceError(
            error="invalid_jws",
            message="Token header is missing kid",
            status_code=400,
            details={},
        )
```

Everything after this (payload validation, authorization check, feedback submission) stays unchanged.

### Step 7: Update test conftest to mock identity_client

**File:** `services/reputation/tests/unit/routers/conftest.py`

The `inject_mock_identity` function currently sets `state.platform_agent` using the mock. It needs to ALSO set `state.identity_client` with a mock that has a `verify_jws` async method.

The key insight: tests that override `validate_certificate` (e.g., with `InvalidSignature`) need the mock `identity_client.verify_jws` to delegate through `platform_agent.validate_certificate` — so both mocks stay in sync.

Add these imports at the top of the file:
```python
import base64
import json
from unittest.mock import AsyncMock
from service_commons.exceptions import ServiceError
```

Add this helper function (before `inject_mock_identity`):

```python
def _make_delegating_verify_jws(state_ref: object) -> Any:
    """Create a verify_jws mock that delegates to platform_agent.validate_certificate.

    This ensures tests that override validate_certificate (e.g. with InvalidSignature)
    will see the same behavior when verification goes through identity_client.verify_jws.

    Signature-related errors (InvalidSignature, ValueError) are mapped to valid=False.
    Connectivity errors (ConnectionError, TimeoutError, etc.) are raised as ServiceError(502).
    """

    async def _delegating_verify_jws(token: str) -> dict[str, object]:
        parts = token.split(".")
        header_b64 = parts[0]
        padded_header = header_b64 + "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(padded_header))
        agent_id = header.get("kid", "")

        try:
            payload = state_ref.platform_agent.validate_certificate(token)
        except (InvalidSignature, ValueError):
            return {"valid": False, "reason": "signature mismatch"}
        except Exception as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        return {"valid": True, "agent_id": agent_id, "payload": payload}

    return _delegating_verify_jws
```

Update `inject_mock_identity` to also set `state.identity_client`:

```python
def inject_mock_identity(
    verify_response: dict[str, object] | None = None,
) -> None:
    """Inject a mock IdentityClient into AppState."""
    state = get_app_state()
    if verify_response is None:
        verify_response = _mock_verify_ok(ALICE_ID, _feedback_payload())

    payload = verify_response.get("payload")
    side_effect: Exception | None = None
    valid = verify_response.get("valid")
    if isinstance(valid, bool) and not valid:
        payload = None
        side_effect = InvalidSignature()

    state.platform_agent = make_mock_platform_agent(
        verify_payload=payload if isinstance(payload, dict) else None,
        verify_side_effect=side_effect,
    )

    # Set up mock identity_client with delegating verify_jws
    mock_identity = AsyncMock()
    mock_identity.close = AsyncMock()
    mock_identity.verify_jws = AsyncMock(
        side_effect=_make_delegating_verify_jws(state),
    )
    state.identity_client = mock_identity
```

Also update the test config YAML in `_isolate_test` to include the identity section:

```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
```

Add these lines after the `platform:` section in the config template string.

**IMPORTANT:** You also need to add the `from typing import Any` import at the top of the conftest file for the `_make_delegating_verify_jws` return type annotation.

### Step 8: Verify

Run from the reputation service directory:
```bash
cd services/reputation && just ci-quiet
```

This runs ALL CI checks: formatting, linting, type checking (mypy + pyright), security scanning, spell checking, custom semgrep rules, and all tests.

ALL existing tests must continue to pass. No test files should be modified.

## Files Summary

| File | Action |
|------|--------|
| `services/reputation/src/reputation_service/config.py` | Add `IdentityConfig` class and optional `identity` field |
| `services/reputation/config.yaml` | Add `identity` section |
| `services/reputation/src/reputation_service/services/identity_client.py` | **NEW** — IdentityClient class |
| `services/reputation/src/reputation_service/core/state.py` | Add `identity_client` field |
| `services/reputation/src/reputation_service/core/lifespan.py` | Initialize and close IdentityClient |
| `services/reputation/src/reputation_service/routers/feedback.py` | Use identity_client.verify_jws instead of platform_agent.validate_certificate |
| `services/reputation/tests/unit/routers/conftest.py` | Add delegating mock, update inject_mock_identity, add identity config |

## Critical Rules

- Do NOT modify any existing test files in `tests/unit/` or `tests/integration/` (except conftest.py fixtures)
- Do NOT use default parameter values for configurable settings
- Import `ServiceError` from `service_commons.exceptions` in the identity_client.py (NOT from `reputation_service.core.exceptions`)
- All Python execution via `uv run` only
- Lines must not exceed 100 characters
- All config must come from config.yaml, never hardcoded
