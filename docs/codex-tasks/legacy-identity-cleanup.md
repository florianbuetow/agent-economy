# Legacy Identity Code Cleanup

**Ticket**: agent-economy-kj6
**Goal**: Remove ALL backwards-compatibility legacy identity code from production AND test files across reputation, court, and central-bank services. All authentication uses `PlatformAgent.validate_certificate()` — legacy shims must go.

**Permission**: Existing test files MAY be modified for this cleanup.

---

## Phase 1: Reputation Service — Production Code

### 1a. `services/reputation/src/reputation_service/config.py`

Remove `LegacyIdentityConfig` class and the `identity` field from `Settings`. Remove the `normalize_identity_platform` model_validator. Make `platform` required (not Optional).

**Before:**
```python
from pydantic import BaseModel, ConfigDict, model_validator

class LegacyIdentityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    verify_jws_path: str
    timeout_seconds: int

class Settings(BaseModel):
    ...
    platform: PlatformConfig | None = None
    identity: LegacyIdentityConfig | None = None

    @model_validator(mode="after")
    def normalize_identity_platform(self) -> Settings:
        ...
```

**After:**
```python
from pydantic import BaseModel, ConfigDict
# Remove model_validator import

# Remove LegacyIdentityConfig class entirely

class Settings(BaseModel):
    ...
    platform: PlatformConfig  # Required, not Optional
    # Remove identity field entirely
    # Remove normalize_identity_platform method entirely
```

### 1b. `services/reputation/src/reputation_service/core/state.py`

Remove the `identity_client` property and setter.

**Remove:**
```python
@property
def identity_client(self) -> PlatformAgent | None:
    """Backward-compatible alias for legacy tests."""
    return self.platform_agent

@identity_client.setter
def identity_client(self, value: PlatformAgent | None) -> None:
    """Backward-compatible alias for legacy tests."""
    self.platform_agent = value
```

### 1c. `services/reputation/src/reputation_service/core/lifespan.py`

Remove the `if settings.platform is None:` guard — platform is now required.

**Before:**
```python
if settings.platform is None:
    msg = "Platform configuration not initialized"
    raise RuntimeError(msg)

if settings.platform.agent_config_path:
```

**After:**
```python
if settings.platform.agent_config_path:
```

### Verification
```bash
cd services/reputation && just ci-quiet
```

Commit: `refactor(reputation): remove LegacyIdentityConfig and legacy identity shims`

---

## Phase 2: Reputation Service — Test Files

### 2a. `services/reputation/tests/unit/test_config.py`

Remove the `test_identity_section_exists` test method (lines 58-64). It tests `settings.identity` which no longer exists.

**Remove:**
```python
def test_identity_section_exists(self) -> None:
    """Settings must have an identity section."""
    settings = get_settings()
    assert settings.identity is not None
    assert isinstance(settings.identity.base_url, str)
    assert isinstance(settings.identity.verify_jws_path, str)
    assert isinstance(settings.identity.timeout_seconds, int)
```

Add a replacement test:
```python
def test_platform_section_exists(self) -> None:
    """Settings must have a platform section with agent_config_path."""
    settings = get_settings()
    assert settings.platform is not None
    assert isinstance(settings.platform.agent_config_path, str)
```

### 2b. `services/reputation/tests/helpers.py`

Remove `make_mock_identity_client` function entirely. All callers should use `make_mock_platform_agent` instead.

**Remove the entire function** `make_mock_identity_client` (lines 52-82).

### 2c. `services/reputation/tests/unit/routers/conftest.py`

Change import from `make_mock_identity_client` to `make_mock_platform_agent`.

**Before:**
```python
from tests.helpers import make_jws_token, make_mock_identity_client
```

**After:**
```python
from tests.helpers import make_jws_token, make_mock_platform_agent
```

Update `inject_mock_identity` function:

**Before:**
```python
def inject_mock_identity(verify_response=None):
    state = get_app_state()
    ...
    state.platform_agent = make_mock_identity_client(verify_response=verify_response)
```

**After:**
```python
def inject_mock_identity(verify_response=None):
    state = get_app_state()
    ...
    # Extract payload from legacy verify_response format if needed
    payload = None
    side_effect = None
    if verify_response is not None:
        valid = verify_response.get("valid")
        payload = verify_response.get("payload")
        if isinstance(valid, bool) and not valid:
            from cryptography.exceptions import InvalidSignature
            side_effect = InvalidSignature()
            payload = None
    state.platform_agent = make_mock_platform_agent(
        verify_payload=payload,
        verify_side_effect=side_effect,
    )
```

### 2d. `services/reputation/tests/unit/routers/test_feedback.py`

Change import and usage:

**Before:**
```python
from tests.helpers import make_jws_token, make_mock_identity_client
...
state.identity_client = make_mock_identity_client(...)
```

**After:**
```python
from tests.helpers import make_jws_token, make_mock_platform_agent
...
state.platform_agent = make_mock_platform_agent(...)
```

The `make_mock_identity_client` was called with `verify_response={"valid": True, "agent_id": ..., "payload": ...}`. The `make_mock_platform_agent` takes `verify_payload=<dict>` directly. So:

```python
# Before:
state.identity_client = make_mock_identity_client(
    verify_response={"valid": True, "agent_id": kid, "payload": payload}
)
# After:
state.platform_agent = make_mock_platform_agent(verify_payload=payload)
```

### 2e. `services/reputation/tests/unit/routers/test_feedback_auth.py`

Same pattern — replace all `make_mock_identity_client` with `make_mock_platform_agent` and `state.identity_client` with `state.platform_agent`.

Key mappings:
- `make_mock_identity_client(verify_response={"valid": True, ...payload...})` → `make_mock_platform_agent(verify_payload=payload)`
- `make_mock_identity_client(verify_response={"valid": False, ...})` → `make_mock_platform_agent(verify_side_effect=InvalidSignature())`
- `make_mock_identity_client(verify_side_effect=ServiceError("forbidden", ...))` → `make_mock_platform_agent(verify_side_effect=InvalidSignature())`
- `make_mock_identity_client(verify_side_effect=ServiceError("identity_service_unavailable", ...))` → `make_mock_platform_agent(verify_side_effect=Exception("Cannot reach Identity service"))`
- `state.identity_client = ...` → `state.platform_agent = ...`

### 2f. `services/reputation/tests/unit/routers/test_identity_error_remapping.py`

Same pattern as 2e. Replace all identity_client references with platform_agent.

**IMPORTANT**: This file tests error remapping from IdentityClient errors. The error contract is:
- `forbidden` (403) → comes from `InvalidSignature` in PlatformAgent
- `identity_service_unavailable` (502) → comes from generic `Exception` in PlatformAgent

The tests mock the error, then verify the router remaps it correctly. The mock side effects must match what PlatformAgent actually raises (not what IdentityClient raised):
- Where the test had `ServiceError("forbidden", ...)` → use `InvalidSignature()`
- Where the test had `ServiceError("identity_service_unavailable", ...)` → use `Exception("Cannot reach Identity service")`

Read the actual `services/reputation/src/reputation_service/routers/feedback.py` to see how it catches exceptions from `platform_agent.validate_certificate()` and maps them to error responses.

### 2g. `services/reputation/tests/unit/test_persistence.py`

Replace `make_mock_identity_client` with `make_mock_platform_agent`, `state.identity_client` with `state.platform_agent`.

### 2h. `services/reputation/tests/integration/test_endpoints.py`

Same pattern — replace identity_client with platform_agent.

### Verification
```bash
cd services/reputation && just ci-quiet
```

Commit: `test(reputation): migrate tests from identity_client to platform_agent`

---

## Phase 3: Court Service — Clean State Aliases

### 3a. `services/court/src/court_service/core/state.py`

Remove the backward-compat property aliases for `central_bank_client`, `reputation_client`, `task_board_client`. These currently delegate to `platform_agent`.

**Remove:**
```python
@property
def central_bank_client(self) -> PlatformAgent | None:
    """Backward-compat alias — delegates to platform_agent."""
    return self.platform_agent

@property
def reputation_client(self) -> PlatformAgent | None:
    """Backward-compat alias — delegates to platform_agent."""
    return self.platform_agent

@property
def task_board_client(self) -> PlatformAgent | None:
    """Backward-compat alias — delegates to platform_agent."""
    return self.platform_agent
```

### 3b. `services/court/tests/helpers.py`

Remove `make_mock_identity_client` function (lines 40-51). It creates a mock with `verify_jws` which is the legacy interface.

Keep `make_mock_task_board_client`, `make_mock_central_bank_client`, `make_mock_reputation_client` — BUT they are only used if tests reference them. Check each test file.

Actually — the court tests in `conftest.py` already use `make_mock_platform_agent` (line 85). The `make_mock_identity_client` is only used in `test_disputes.py` at lines 215 and 1061 where it sets `state.identity_client`. Since court's state no longer has `identity_client`, these lines must change to use `state.platform_agent = make_mock_platform_agent(...)`.

### 3c. `services/court/tests/unit/routers/test_disputes.py`

Replace:
- `from tests.helpers import ... make_mock_identity_client ...` → remove `make_mock_identity_client` from import
- `state.identity_client = make_mock_identity_client(...)` → `state.platform_agent = make_mock_platform_agent(...)`
- `state.central_bank_client.split_escrow` → `state.platform_agent.split_escrow`
- `state.reputation_client.record_feedback` → `state.platform_agent.record_feedback` (or `submit_platform_feedback`)

**IMPORTANT**: Check what attribute name the test actually accesses. The mock from `make_mock_platform_agent` has:
- `mock.split_escrow` (AsyncMock)
- `mock.submit_platform_feedback` (AsyncMock)
- `mock.record_feedback` (alias for submit_platform_feedback)

So `state.platform_agent.split_escrow` and `state.platform_agent.record_feedback` will work.

### 3d. `services/court/tests/unit/routers/conftest.py`

Already uses `make_mock_platform_agent`. No changes needed. BUT verify that all helper functions that access `state.central_bank_client` or `state.reputation_client` are updated to use `state.platform_agent`.

Actually looking at conftest.py more carefully:
- `inject_central_bank_error` accesses `state.platform_agent.split_escrow.side_effect` — already correct
- `inject_reputation_error` accesses `state.platform_agent.submit_platform_feedback.side_effect` — already correct

No changes needed to court conftest.py.

### 3e. `services/court/tests/unit/test_ruling_orchestrator.py`

Check if this file uses legacy client references and update accordingly.

### Verification
```bash
cd services/court && just ci-quiet
```

Commit: `refactor(court): remove legacy state aliases and identity_client from tests`

---

## Phase 4: Central Bank — Test Cleanup Only

The central-bank `IdentityClient` is NOT legacy (it does agent lookup via `GET /agents/{id}`). But the TESTS use `state.identity_client.verify_jws` which is setting up a mock for the old verification path. The production code in `routers/helpers.py` uses `state.platform_agent.validate_certificate()`.

### 4a. `services/central-bank/tests/unit/routers/conftest.py`

The conftest creates `mock_identity` with `verify_jws` and `get_agent` methods, then assigns `state.identity_client = mock_identity`. The `verify_jws` is dead — production uses `platform_agent.validate_certificate()`. The `get_agent` IS used by production code.

Update: keep `state.identity_client` assignment (it's a real IdentityClient for agent lookup), but remove any `verify_jws` setup. Add `state.platform_agent` mock setup (already exists on line 112-116).

Actually, the conftest already sets up both `state.identity_client` (for get_agent) AND `state.platform_agent` (for validate_certificate). This is correct — identity_client is a DIFFERENT client from platform_agent. No changes needed to conftest.

### 4b. `services/central-bank/tests/unit/routers/test_accounts.py`, `test_escrow.py`, `test_review_fixes.py`, `test_self_service_account.py`

These tests set `state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)`. This is setting up a mock for the OLD verification path. Since production now uses `state.platform_agent.validate_certificate()`, these verify_jws mocks are dead code.

However, removing them requires understanding how the test tokens are verified. The tests create JWS tokens and need verification to work. Since the conftest already mocks `platform_agent.validate_certificate`, the verify_jws mock is indeed dead.

**Changes**: Remove all `state.identity_client.verify_jws = AsyncMock(...)` lines from test files. The tests should still pass because `platform_agent.validate_certificate` is already mocked in conftest.

BUT: Be careful. Some tests might create custom verify behavior for specific test cases (e.g., tampered tokens, wrong signatures). Read each test carefully to understand whether removing the verify_jws mock changes test behavior.

**Strategy**: Remove `identity_client.verify_jws` mocks line by line. Run tests after each file change. If tests fail, investigate and fix.

### Verification
```bash
cd services/central-bank && just ci-quiet
```

Commit: `test(central-bank): remove dead verify_jws mocks from tests`

---

## Execution Order

1. Phase 1 → Phase 2 → verify reputation with `just ci-quiet`
2. Phase 3 → verify court with `just ci-quiet`
3. Phase 4 → verify central-bank with `just ci-quiet`
4. Final: run `just ci-all-quiet` from project root (task-board mypy error is pre-existing, ignore it)

---

## Rules

- Use `uv run` for all Python execution — never use raw python, python3, or pip install
- Run `just ci-quiet` from the service directory after each phase
- Read each file before modifying it — understand the full context
- When replacing `identity_client` with `platform_agent`, ensure the mock has the right methods
- `make_mock_platform_agent` returns a MagicMock with `validate_certificate` (sync), `close` (async), `get_task` (async), `split_escrow` (async), `record_ruling` (async), `submit_platform_feedback` (async), `record_feedback` (alias)
- `make_mock_identity_client` returns a MagicMock with `verify_jws` (async), `close` (async)
- The production `validate_certificate()` is SYNCHRONOUS (it's a local Ed25519 verify), not async
- Commit after each phase passes CI
