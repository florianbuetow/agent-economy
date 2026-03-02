# Replace Identity-Service JWS Verification with Local PlatformAgent — Codex Implementation Plan

## Context

All five services currently delegate JWS token verification to the Identity service via `IdentityClient.verify_jws()`, which makes an HTTP call to `POST /agents/verify-jws`. This is unnecessary — each service can verify tokens locally using `PlatformAgent.validate_certificate()` from the `base_agent` SDK, which does Ed25519 signature verification with the platform's public key. No network round-trip needed.

The Court service already has the correct pattern in `verify_platform_token()` (see `services/court/src/court_service/routers/validation.py:47-82`). We are extending this pattern to Central Bank, Task Board, and Reputation, then cleaning up the Court's remaining legacy `verify_jws()` function.

## Reference Implementation (Court service — already working)

The Court's `verify_platform_token()` in `routers/validation.py:47-82` is the canonical pattern:

```python
def verify_platform_token(token: str, platform_agent: PlatformAgent | None) -> dict[str, Any]:
    if platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)
    try:
        payload = platform_agent.validate_certificate(token)
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {}) from exc
    except Exception as exc:
        raise ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "Cannot reach Identity service", 502, {}) from exc
    if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ServiceError("INVALID_PAYLOAD", "JWS payload must be a JSON object", 400, {})
    return payload
```

Key details:
- `validate_certificate()` is **synchronous** (not async)
- It returns `dict[str, object]` on success
- It raises `InvalidSignature` or `ValueError` on failure
- The catch-all `Exception` handler returns `IDENTITY_SERVICE_UNAVAILABLE` for backward-compat with existing tests
- The payload contains an `agent_id` field (from the `kid` header) accessible via `payload.get("agent_id")` or from the JWS header

**IMPORTANT**: `validate_certificate()` returns the JWS **payload** directly as a dict. The old `IdentityClient.verify_jws()` returned `{"valid": True, "agent_id": "...", "payload": {...}}` — a wrapper dict. Code that used `verified["agent_id"]` and `verified["payload"]` must be updated to read from the payload directly. The agent_id (signer) is embedded in the JWS header as `kid` and available in the payload if the signer put it there, OR it can be extracted from the JWS header.

## How the agent_id (signer identity) works

The old `IdentityClient.verify_jws()` returned `agent_id` as a top-level field. With local verification via `validate_certificate()`, the signer identity comes from the JWS `kid` header.

To extract the `kid` from a JWS token:
```python
import base64, json
def extract_kid_from_jws(token: str) -> str:
    header_b64 = token.split(".", maxsplit=1)[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    return header.get("kid", "")
```

This is needed in services where the signer identity matters (Central Bank checks `agent_id` for platform-only operations; Reputation checks `from_agent_id` matches signer).

---

## Phase 1: Central Bank (ticket agent-economy-2p2)

### Overview
Central Bank uses `IdentityClient.verify_jws()` in `routers/helpers.py:verify_jws_token()`, which is called from `routers/accounts.py` (4 endpoints) and `routers/escrow.py` (3 endpoints). All 7 callsites use the pattern `verified = await verify_jws_token(token)` then access `verified["agent_id"]` and `verified["payload"]`.

Central Bank also uses `IdentityClient.get_agent()` in `accounts.py:create_account` to verify agent existence — this must be KEPT. So we keep the `IdentityClient` class but remove the `verify_jws` method.

### Step 1.1: Update `config.py` — Remove `verify_jws_path` from IdentityConfig

**File**: `services/central-bank/src/central_bank_service/config.py`

Change `IdentityConfig` to remove `verify_jws_path`:
```python
class IdentityConfig(BaseModel):
    """Identity service connection configuration."""
    model_config = ConfigDict(extra="forbid")
    base_url: str
    get_agent_path: str
```

### Step 1.2: Update `config.yaml` — Remove `verify_jws_path`

**File**: `services/central-bank/config.yaml`

Remove the `verify_jws_path` line from the `identity:` section:
```yaml
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
```

### Step 1.3: Update `services/identity_client.py` — Remove `verify_jws` method

**File**: `services/central-bank/src/central_bank_service/services/identity_client.py`

Remove the `verify_jws()` method entirely. Remove the `verify_jws_path` constructor parameter. Keep `get_agent()` and `close()`.

The constructor becomes:
```python
def __init__(self, base_url: str, get_agent_path: str) -> None:
    self._base_url = base_url
    self._get_agent_path = get_agent_path
    self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
```

### Step 1.4: Update `core/lifespan.py` — Remove `verify_jws_path` from IdentityClient init

**File**: `services/central-bank/src/central_bank_service/core/lifespan.py`

Change the IdentityClient construction to remove `verify_jws_path`:
```python
state.identity_client = IdentityClient(
    base_url=settings.identity.base_url,
    get_agent_path=settings.identity.get_agent_path,
)
```

### Step 1.5: Rewrite `routers/helpers.py:verify_jws_token()` — Use PlatformAgent

**File**: `services/central-bank/src/central_bank_service/routers/helpers.py`

Replace the async `verify_jws_token()` function with a **synchronous** function that uses `PlatformAgent.validate_certificate()`. The function must still return `{"agent_id": ..., "payload": ...}` to match what all callsites expect.

Add these imports at the top:
```python
import base64
from cryptography.exceptions import InvalidSignature
```

Replace the function:
```python
def verify_jws_token(token: str) -> dict[str, Any]:
    """Verify a JWS token locally using the platform agent's public key.

    Returns {"agent_id": str, "payload": dict} for backward compatibility.
    """
    state = get_app_state()
    if state.platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)

    try:
        payload = state.platform_agent.validate_certificate(token)
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError(
            "FORBIDDEN",
            "JWS signature verification failed",
            403,
            {},
        ) from exc
    except Exception as exc:
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Cannot reach Identity service",
            502,
            {},
        ) from exc

    if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ServiceError("INVALID_PAYLOAD", "JWS payload must be a JSON object", 400, {})

    # Extract agent_id from JWS header kid field
    header_b64 = token.split(".", maxsplit=1)[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    agent_id = header.get("kid", "")

    return {"agent_id": agent_id, "payload": payload}
```

**CRITICAL**: This function is now **synchronous** (not async). All callsites in `accounts.py` and `escrow.py` that do `await verify_jws_token(...)` must be changed to `verify_jws_token(...)` (remove the `await`).

### Step 1.6: Update `routers/accounts.py` — Remove `await` from verify_jws_token calls

**File**: `services/central-bank/src/central_bank_service/routers/accounts.py`

4 callsites. Change each from:
```python
verified = await verify_jws_token(data["token"])
```
to:
```python
verified = verify_jws_token(data["token"])
```

Also change the `await verify_jws_token(token)` calls (the Bearer token path).

### Step 1.7: Update `routers/escrow.py` — Remove `await` from verify_jws_token calls

**File**: `services/central-bank/src/central_bank_service/routers/escrow.py`

3 callsites. Same change: remove `await`.

### Step 1.8: Update test config — Remove `verify_jws_path` from test configs

**File**: `services/central-bank/tests/unit/routers/conftest.py`

In the `_valid_config` / config template string, remove the `verify_jws_path` line from the `identity:` section.

**File**: `services/central-bank/tests/unit/test_config.py`

Any test config YAML that includes `verify_jws_path` under `identity:` must have it removed. Read this file first — if it creates config YAML strings with `verify_jws_path`, remove that line.

### Step 1.9: Update test fixtures — Mock PlatformAgent instead of IdentityClient.verify_jws

**File**: `services/central-bank/tests/unit/routers/conftest.py`

The `app` fixture currently creates a `mock_identity` AsyncMock and sets `state.identity_client = mock_identity`. Since `verify_jws_token()` now uses `state.platform_agent.validate_certificate()`:

1. Create a mock PlatformAgent using the same pattern as Court's `make_mock_platform_agent()` from `services/court/tests/helpers.py`
2. Set `state.platform_agent = mock_platform_agent`
3. Keep `state.identity_client` as a mock since `get_agent()` is still used in create_account
4. The mock's `validate_certificate` should decode the JWS body from a real signed token (since the tests use `make_jws_token` with real Ed25519 keys)

Since Central Bank tests use real Ed25519 keypairs (via `joserfc`), `validate_certificate()` needs to actually verify the signature. The cleanest approach: use a real `BaseAgent` or `PlatformAgent` instance initialized with the test keypair. But since the tests use `make_jws_token` which creates real signed tokens, you can create a real PlatformAgent from the same keypair.

ALTERNATIVE simpler approach: make the mock's `validate_certificate` decode the JWS payload from the token (same as Court's `_decode_jws_body` pattern in helpers.py) without actual crypto verification. The tests already create structurally valid JWS tokens. Add a helper function to the conftest:

```python
import base64
import json

def _decode_jws_payload(token: str) -> dict[str, Any]:
    """Decode the base64url payload from a JWS token (no signature verification)."""
    parts = token.split(".")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
```

Then in the `app` fixture:
```python
from unittest.mock import MagicMock

mock_platform = MagicMock()
mock_platform.agent_id = PLATFORM_AGENT_ID
mock_platform.validate_certificate = MagicMock(side_effect=_decode_jws_payload)
mock_platform.close = AsyncMock()
state.platform_agent = mock_platform
```

### Step 1.10: Verify

Run from `services/central-bank/`:
```bash
just ci-quiet
```

ALL checks must pass with zero failures.

---

## Phase 2: Task Board (ticket agent-economy-fqv)

### Overview
Task Board uses `IdentityClient` solely for JWS verification. The `IdentityClient` class is in `clients/identity_client.py`. It's consumed by `TokenValidator` which is injected into `TaskManager`. Additionally, `TaskManager` receives `identity_client` directly (used in `validate_jws_token`).

### Step 2.1: Update `config.py` — Remove `verify_jws_path` and `timeout_seconds` from IdentityConfig

**File**: `services/task-board/src/task_board_service/config.py`

Remove the entire `IdentityConfig` class. Remove the `identity` field from the `Settings` class.

### Step 2.2: Update `config.yaml` — Remove `identity` section

**File**: `services/task-board/config.yaml`

Remove the entire `identity:` block.

### Step 2.3: Delete `clients/identity_client.py`

**File**: `services/task-board/src/task_board_service/clients/identity_client.py`

Delete this file entirely. The Task Board IdentityClient is only used for `verify_jws`.

### Step 2.4: Rewrite `services/token_validator.py` — Use PlatformAgent

**File**: `services/task-board/src/task_board_service/services/token_validator.py`

Replace the `IdentityClient` dependency with `PlatformAgent`. The `TokenValidator.__init__` changes to accept a `PlatformAgent` instead of `IdentityClient`.

Change the `validate_jws_token` method from async to sync (since `validate_certificate` is sync). BUT — since this is called with `await` in many places, check all callsites first. If the method is awaited, keep it async but make the actual verification sync inside.

Actually — the simplest approach: keep `validate_jws_token` as `async` for minimal callsite disruption, but replace the body:

```python
from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, cast

from cryptography.exceptions import InvalidSignature
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent
```

Constructor:
```python
def __init__(self, platform_agent: PlatformAgent) -> None:
    self._platform_agent = platform_agent
```

In `validate_jws_token`:
- Steps 4 (format validation) stay the same
- Steps 5-6 (Identity service call) replaced with:
```python
try:
    payload = self._platform_agent.validate_certificate(token)
except (InvalidSignature, ValueError) as exc:
    raise ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {}) from exc
except Exception as exc:
    raise ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "Cannot connect to Identity service", 502, {}) from exc

if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
    raise ServiceError("INVALID_JWS", "Token payload is not a valid JSON object", 400, {})

# Extract kid from header
header = decode_base64url_json(parts[0], "header")
kid = header.get("kid")
if not isinstance(kid, str) or len(kid) < 1:
    raise ServiceError("INVALID_JWS", "Token header is missing kid", 400, {})
agent_id = kid
```

Remove the `isinstance(result, dict)` block and the fallback decoding. Remove the `_tampered` check (this was a test-only hack for IdentityClient mocking).

Remove the `TYPE_CHECKING` import of `IdentityClient`.

### Step 2.5: Update `core/lifespan.py` — Remove IdentityClient, pass PlatformAgent to TokenValidator

**File**: `services/task-board/src/task_board_service/core/lifespan.py`

1. Remove `from task_board_service.clients.identity_client import IdentityClient`
2. Remove the IdentityClient construction block (lines ~116-121)
3. Remove `state.identity_client = identity_client`
4. Change TokenValidator construction: `token_validator = TokenValidator(platform_agent=platform_agent)` (where `platform_agent` is `state.platform_agent`)
5. Remove `identity_client` parameter from `TaskManager` constructor
6. Remove `await identity_client.close()` from shutdown
7. If `platform_agent` is None at that point (no `agent_config_path`), handle it — but in practice `agent_config_path` is always set.

The `TaskManager` also takes `identity_client` parameter — check if it's used there.

### Step 2.6: Check TaskManager for IdentityClient usage

**File**: `services/task-board/src/task_board_service/services/task_manager.py`

Read this file. If `TaskManager.__init__` takes `identity_client` parameter, remove it. If it uses `self._identity_client` anywhere, update those usages.

### Step 2.7: Update `core/state.py` — Remove identity_client field

**File**: `services/task-board/src/task_board_service/core/state.py`

Remove `identity_client: IdentityClient | None = None` from `AppState`.
Remove the `TYPE_CHECKING` import of `IdentityClient`.

### Step 2.8: Update test config — Remove `identity` section from test configs

**File**: `services/task-board/tests/unit/routers/conftest.py`

Remove `identity:` block from the config YAML template.

**File**: `services/task-board/tests/unit/test_config.py`

Remove `identity:` from any test config YAML that includes it.

### Step 2.9: Update test fixtures — Mock PlatformAgent instead of IdentityClient

**File**: `services/task-board/tests/unit/routers/conftest.py`

1. Remove mock_identity creation and `state.identity_client = mock_identity`
2. Create a mock PlatformAgent with `validate_certificate` that decodes JWS payload
3. Set `state.platform_agent = mock_platform`
4. Update `state.token_validator._identity_client` → `state.token_validator._platform_agent` propagation
5. Remove `state.task_manager._identity_client` propagation
6. Remove `mock_identity_verify_success`, `mock_identity_unavailable`, `mock_identity_timeout`, `mock_identity_unexpected_response` fixtures — or replace them with platform_agent equivalents

### Step 2.10: Update test helpers

**File**: `services/task-board/tests/helpers.py`

Read and check if this needs updates.

### Step 2.11: Verify

Run from `services/task-board/`:
```bash
just ci-quiet
```

ALL checks must pass with zero failures.

---

## Phase 3: Reputation (ticket agent-economy-vbm)

### Overview
Reputation is the simplest case. `IdentityClient.verify_jws()` is called directly in `routers/feedback.py:submit_feedback_endpoint` (line 120). No intermediate validator class.

### Step 3.1: Update `config.py` — Remove IdentityConfig or replace with platform config

**File**: `services/reputation/src/reputation_service/config.py`

Remove the entire `IdentityConfig` class. Remove `identity: IdentityConfig` from `Settings`. Add a `PlatformConfig`:
```python
class PlatformConfig(BaseModel):
    """Platform agent configuration."""
    model_config = ConfigDict(extra="forbid")
    agent_config_path: str
```

Add `platform: PlatformConfig` to `Settings`.

### Step 3.2: Update `config.yaml` — Replace `identity` with `platform`

**File**: `services/reputation/config.yaml`

Remove:
```yaml
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
```

Add:
```yaml
platform:
  agent_config_path: "../../agents/config.yaml"
```

### Step 3.3: Delete or gut `services/identity_client.py`

**File**: `services/reputation/src/reputation_service/services/identity_client.py`

Delete this file entirely — Reputation has no other use for the Identity client.

### Step 3.4: Update `core/state.py` — Replace identity_client with platform_agent

**File**: `services/reputation/src/reputation_service/core/state.py`

Replace `identity_client: IdentityClient | None = None` with `platform_agent: PlatformAgent | None = None`.
Update TYPE_CHECKING imports accordingly.

### Step 3.5: Update `core/lifespan.py` — Initialize PlatformAgent instead of IdentityClient

**File**: `services/reputation/src/reputation_service/core/lifespan.py`

Remove IdentityClient import and construction. Add PlatformAgent initialization:
```python
from pathlib import Path
from base_agent.factory import AgentFactory
from reputation_service.config import get_config_path

# In lifespan:
if settings.platform.agent_config_path:
    config_path = Path(settings.platform.agent_config_path)
    if not config_path.is_absolute():
        config_path = Path(get_config_path()).parent / config_path
    factory = AgentFactory(config_path=config_path)
    platform_agent = factory.platform_agent()
    await platform_agent.register()
    state.platform_agent = platform_agent
```

Update shutdown to close platform_agent instead of identity_client.

### Step 3.6: Update `routers/feedback.py` — Replace identity_client.verify_jws with platform_agent.validate_certificate

**File**: `services/reputation/src/reputation_service/routers/feedback.py`

In `submit_feedback_endpoint`:
1. Replace `state.identity_client` check with `state.platform_agent` check
2. Replace the verify_jws call:

Old:
```python
verified = await state.identity_client.verify_jws(token)
payload: dict[str, object] = verified["payload"]
signer_agent_id: str = verified["agent_id"]
```

New:
```python
import base64
from cryptography.exceptions import InvalidSignature

# Verify token locally
if state.platform_agent is None:
    msg = "Platform agent not initialized"
    raise RuntimeError(msg)

try:
    payload_raw = state.platform_agent.validate_certificate(token)
except (InvalidSignature, ValueError) as exc:
    raise ServiceError(
        error="FORBIDDEN",
        message="JWS signature verification failed",
        status_code=403,
        details={},
    ) from exc
except Exception as exc:
    raise ServiceError(
        error="IDENTITY_SERVICE_UNAVAILABLE",
        message="Cannot reach Identity service",
        status_code=502,
        details={},
    ) from exc

payload: dict[str, object] = payload_raw  # type: ignore[assignment]

# Extract signer from JWS header kid
header_b64 = token.split(".", maxsplit=1)[0]
padded = header_b64 + "=" * (-len(header_b64) % 4)
header = json.loads(base64.urlsafe_b64decode(padded))
signer_agent_id: str = header.get("kid", "")
```

### Step 3.7: Update test config — Replace `identity` with `platform`

**File**: `services/reputation/tests/unit/routers/conftest.py`

In the config YAML template, replace the `identity:` block with `platform:` block:
```yaml
platform:
  agent_config_path: ""
```

**File**: `services/reputation/tests/unit/test_config.py`

Same change in any config templates.

### Step 3.8: Update test fixtures — Mock PlatformAgent instead of IdentityClient

**File**: `services/reputation/tests/unit/routers/conftest.py`

Update `inject_mock_identity` → `inject_mock_platform_agent` using the mock pattern from Court helpers.

**File**: `services/reputation/tests/helpers.py`

Read this file and update `make_mock_identity_client` to `make_mock_platform_agent` or add a new helper.

### Step 3.9: Update test files that reference identity_client

Check all test files in `services/reputation/tests/` for references to `identity_client`, `verify_jws`, `make_mock_identity_client`, etc. Update accordingly. Remember: do NOT modify existing test files — add new files if needed. BUT: the conftest.py and helpers.py are infrastructure, not acceptance tests, so they CAN be modified.

### Step 3.10: Verify

Run from `services/reputation/`:
```bash
just ci-quiet
```

ALL checks must pass with zero failures.

---

## Phase 4: Court Cleanup (ticket agent-economy-m3r)

### Overview
Court already uses `verify_platform_token()` correctly. The remaining issue: `routers/validation.py` still has an `async verify_jws()` function (lines 85-124) that calls `identity_client.verify_jws()`. The `identity_client` is still initialized in lifespan and stored in AppState. Remove all of this.

### Step 4.1: Remove `verify_jws()` from `routers/validation.py`

**File**: `services/court/src/court_service/routers/validation.py`

Delete the `async def verify_jws(...)` function (lines 85-124). Remove the `TYPE_CHECKING` import of `IdentityClient`.

### Step 4.2: Check that no router imports `verify_jws` from validation

Grep for `from court_service.routers.validation import` and verify `verify_jws` is not imported anywhere. The `disputes.py` router imports `verify_platform_token` (correct).

### Step 4.3: Delete `services/identity_client.py`

**File**: `services/court/src/court_service/services/identity_client.py`

Delete this file. Court's IdentityClient is only used for verify_jws.

### Step 4.4: Update `config.py` — Remove IdentityConfig

**File**: `services/court/src/court_service/config.py`

Remove `IdentityConfig` class. Remove `identity: IdentityConfig` from `Settings`.

### Step 4.5: Update `config.yaml` — Remove `identity` section

**File**: `services/court/config.yaml`

Remove the `identity:` block.

### Step 4.6: Update `core/state.py` — Remove identity_client

**File**: `services/court/src/court_service/core/state.py`

Remove `identity_client: IdentityClient | None = None` from `AppState`.
Remove the `TYPE_CHECKING` import of `IdentityClient`.

### Step 4.7: Update `core/lifespan.py` — Remove IdentityClient init and shutdown

**File**: `services/court/src/court_service/core/lifespan.py`

Remove `from court_service.services.identity_client import IdentityClient`.
Remove the `state.identity_client = IdentityClient(...)` block.
Remove `if state.identity_client is not None: await state.identity_client.close()` from shutdown.

### Step 4.8: Update test config — Remove `identity` section

**File**: `services/court/tests/unit/routers/conftest.py`

Remove the `identity:` block from the config YAML template.

**File**: `services/court/tests/unit/test_config.py`

Remove `identity:` from any test config templates.

### Step 4.9: Update test fixtures — Remove identity_client mock

**File**: `services/court/tests/unit/routers/conftest.py`

Remove `state.identity_client = make_mock_identity_client(...)` from the `app` fixture.

**File**: `services/court/tests/helpers.py`

Remove `make_mock_identity_client()` if no longer used anywhere. Check all imports first.

### Step 4.10: Verify

Run from `services/court/`:
```bash
just ci-quiet
```

ALL checks must pass with zero failures.

---

## Phase 5: Update Test Fixtures (ticket agent-economy-k6w)

This phase covers any remaining test fixture updates. Most of this work is done in Phases 1-4 above. After completing the service changes, run:

```bash
cd /path/to/project && just ci-all-quiet
```

If any tests fail due to stale references to `IdentityClient`, `verify_jws`, `identity_client`, or `IDENTITY_SERVICE_UNAVAILABLE`:
1. Update the test conftest/helpers to mock `platform_agent.validate_certificate` instead
2. Remove test cases that specifically test `IDENTITY_SERVICE_UNAVAILABLE` if they test the old HTTP-call path
3. Keep `IDENTITY_SERVICE_UNAVAILABLE` test cases if they test the catch-all Exception handler in the new code

---

## Execution Order

1. **Phase 4 (Court cleanup)** — simplest, just removing dead code
2. **Phase 3 (Reputation)** — simple, single callsite
3. **Phase 1 (Central Bank)** — moderate complexity, 7 callsites but uniform pattern
4. **Phase 2 (Task Board)** — most complex, TokenValidator indirection

After each phase, run `just ci-quiet` in that service directory.
After ALL phases, run `just ci-all-quiet` from the project root.

---

## Rules

- Use `uv run` for all Python execution, never raw `python` or `pip install`
- Do NOT modify acceptance test files (test_*.py) — only modify conftest.py and helpers.py which are test infrastructure
- If you need to add a new test helper, add it to the service's `tests/helpers.py`
- Every config value comes from config.yaml — no hardcoded defaults
- All Pydantic models use `ConfigDict(extra="forbid")`
- After EACH phase, run `just ci-quiet` from that service directory
- After ALL phases, run `just ci-all-quiet` from the project root
- Commit after each phase with a descriptive message
