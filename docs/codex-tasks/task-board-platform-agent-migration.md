# Task Board PlatformAgent Migration — Codex Execution Plan

## Context

The Task Board service still uses `IdentityClient` (now deleted) and a legacy compatibility shim in `TokenValidator` for JWS verification. The `PlatformAgent` is already instantiated in lifespan and `base-agent` is already a dependency. This plan removes all legacy Identity-service verification code and simplifies to use only `PlatformAgent.validate_certificate()`.

## Current State

Run `cd services/task-board && just ci-quiet` — all checks currently pass. The goal is to remove legacy compat layers while keeping all tests green.

## Rules

- Use `uv run` for all Python execution, never raw `python` or `pip install`
- Run `just ci-quiet` from `services/task-board/` after completing each phase
- Do NOT touch files outside the `services/task-board/` directory
- Work through phases in order — each phase builds on the previous

---

## Phase 1: Simplify TokenValidator

### File: `services/task-board/src/task_board_service/services/token_validator.py`

The `TokenValidator.__init__` currently accepts `*args, **kwargs` with both `platform_agent` and `identity_client` parameters. The `_verify_signature` method has two branches: a legacy `_legacy_identity_client` path and a `_platform_agent` path.

**Changes:**

1. Simplify `__init__` to accept only `platform_agent`:
   ```python
   def __init__(self, platform_agent: PlatformAgent) -> None:
       self._platform_agent = platform_agent
   ```

2. Delete `self._legacy_identity_client` field entirely.

3. Delete the `set_legacy_identity_client()` method entirely.

4. In `_verify_signature`, delete the entire `if self._legacy_identity_client is not None:` branch (the block that calls `await self._legacy_identity_client.verify_jws(token)`). Keep only the `self._platform_agent.validate_certificate(token)` path. Since `platform_agent` is now always required, you can remove the `if self._platform_agent is not None:` guard too — just call it directly.

5. Update the import: the `PlatformAgent` import should move out of `TYPE_CHECKING` if it's now used at runtime in the `__init__` signature. Check if it's already imported at runtime.

6. Make sure the method still returns `dict[str, object]` from `validate_certificate()`.

### Verification
```bash
cd services/task-board && just ci-quiet
```
Tests WILL fail at this point because conftest.py still wires the legacy path. Continue to Phase 2.

---

## Phase 2: Clean up AppState

### File: `services/task-board/src/task_board_service/core/state.py`

1. Delete `_identity_client_compat: Any | None = None` field.
2. Delete the `@property` getter for `identity_client`.
3. Delete the `@identity_client.setter`.
4. Remove any imports that are now unused (e.g., `Any` if only used for identity_client).

### Verification
Tests will still fail — continue to Phase 3.

---

## Phase 3: Clean up config.py

### File: `services/task-board/src/task_board_service/config.py`

1. Delete the `LegacyIdentityConfig` class entirely.
2. Delete `identity: LegacyIdentityConfig | None = None` from the `Settings` class.
3. Delete the `normalize_legacy_identity` model_validator method.
4. Remove any imports that are now unused.

### Verification
Tests will still fail — continue to Phase 4.

---

## Phase 4: Clean up lifespan.py and TaskManager

### File: `services/task-board/src/task_board_service/core/lifespan.py`

1. Find where `TaskManager(...)` is instantiated. Remove the `identity_client=state.identity_client` kwarg from that call.
2. If there's any remaining `IdentityClient` initialization code, remove it.
3. Remove unused imports.

### File: `services/task-board/src/task_board_service/services/task_manager.py`

1. Remove the `identity_client` parameter from `TaskManager.__init__`.
2. Remove `self._identity_client = identity_client` assignment.
3. Remove any imports or type annotations related to `IdentityClient` or `identity_client`.

### Verification
Tests will still fail — continue to Phase 5.

---

## Phase 5: Update test infrastructure

### File: `services/task-board/tests/unit/routers/conftest.py`

This is the critical file. The app fixture currently:
- Creates `mock_identity = AsyncMock()` and sets `state.identity_client = mock_identity`
- Creates `mock_platform` with `validate_certificate = MagicMock(side_effect=_extract_payload)`
- Calls `state.token_validator.set_legacy_identity_client(mock_identity)`
- Has fixtures `mock_identity_verify_success`, `mock_identity_unavailable`, `mock_identity_timeout`, `mock_identity_unexpected_response`

**Changes:**

1. In the `app` fixture: Remove `mock_identity = AsyncMock()` and `state.identity_client = mock_identity`.
2. Remove the `state.token_validator.set_legacy_identity_client(mock_identity)` line.
3. The `mock_platform` setup with `validate_certificate = MagicMock(side_effect=_extract_payload)` is already correct — keep it.
4. Ensure `TokenValidator` is constructed with `platform_agent=state.platform_agent` (it should be, via lifespan). If the test app fixture bypasses lifespan and creates TokenValidator manually, update that construction.
5. Update the mock override fixtures:
   - `mock_identity_verify_success` → Rename/update to configure `state.platform_agent.validate_certificate` to return the decoded payload (it likely already does this via `_extract_payload`).
   - `mock_identity_unavailable` → Configure `state.platform_agent.validate_certificate = MagicMock(side_effect=ConnectionError(...))`.
   - `mock_identity_timeout` → Configure `state.platform_agent.validate_certificate = MagicMock(side_effect=TimeoutError(...))`.
   - `mock_identity_unexpected_response` → Configure `state.platform_agent.validate_certificate = MagicMock(side_effect=ValueError(...))`.
6. Remove the `_extract_kid` and `_extract_payload` helper functions ONLY if they are no longer referenced anywhere. If `_extract_payload` is used as the `side_effect` for `validate_certificate`, keep it.

### File: `services/task-board/tests/unit/test_config.py`

If tests reference `identity:` config sections with `verify_jws_path`, remove those config lines from the inline YAML strings. The `identity` section should be removed entirely from test config fixtures since `LegacyIdentityConfig` no longer exists.

### File: `services/task-board/tests/unit/test_state.py`

If there's an assertion like `assert state.identity_client is None`, remove or update it since the property no longer exists.

### File: `services/task-board/tests/unit/routers/test_auth.py`

Find all occurrences of `state.identity_client.verify_jws = AsyncMock(...)` and replace with the equivalent PlatformAgent mock:

```python
from cryptography.exceptions import InvalidSignature
from unittest.mock import MagicMock

# For "verification fails" scenarios:
state.platform_agent.validate_certificate = MagicMock(side_effect=InvalidSignature())

# For "verification succeeds with specific payload" scenarios:
state.platform_agent.validate_certificate = MagicMock(return_value=payload_dict)

# For "identity unavailable" scenarios:
state.platform_agent.validate_certificate = MagicMock(side_effect=ConnectionError("unavailable"))
```

### File: `services/task-board/tests/unit/routers/test_tasks.py`

Same pattern as test_auth.py — find and replace `state.identity_client.verify_jws` mocks with `state.platform_agent.validate_certificate` equivalents.

### File: `services/task-board/tests/unit/test_token_validator.py`

All 13 tests construct `TokenValidator(identity_client=mock_identity)`. Update to `TokenValidator(platform_agent=mock_platform)` where `mock_platform` has `validate_certificate` configured appropriately. The test assertions about decoded payloads should remain the same.

### Verification
```bash
cd services/task-board && just ci-quiet
```

---

## Phase 6: Delete stale `__pycache__` for deleted IdentityClient

### File: `services/task-board/src/task_board_service/clients/__pycache__/identity_client.cpython-313.pyc`

Delete this stale bytecode file if it exists:
```bash
rm -f src/task_board_service/clients/__pycache__/identity_client.cpython-313.pyc
```

---

## Final Verification

After all phases, run from `services/task-board/`:
```bash
just ci-quiet
```

ALL checks must pass:
- code-format, code-style, code-typecheck, code-lspchecks
- code-security, code-deptry, code-spell, code-semgrep, code-audit
- unit tests (all must pass)

Report the full ci-quiet result.
