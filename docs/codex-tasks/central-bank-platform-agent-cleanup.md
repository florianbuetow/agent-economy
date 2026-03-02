# Central Bank PlatformAgent Cleanup — Codex Execution Plan

## Context

The Central Bank service has been partially migrated to use `PlatformAgent.validate_certificate()` for local Ed25519 verification instead of calling the Identity service via `IdentityClient.verify_jws()`. The core production code works. This plan removes leftover legacy shims, dead config fields, and updates tests that depend on the removed code.

## Current State

Run `cd services/central-bank && just ci-quiet` — all checks currently pass. After each change below, re-run to confirm nothing breaks.

## Rules

- Use `uv run` for all Python execution, never raw `python` or `pip install`
- Run `just ci-quiet` from `services/central-bank/` after EACH task to verify
- Do NOT touch files outside the `services/central-bank/` directory
- The verification helper tests in `test_review_fixes.py` MUST be updated (not deleted) to use the new PlatformAgent mock pattern

---

## Task 1: Delete the orphaned legacy shim in helpers.py

### File: `services/central-bank/src/central_bank_service/routers/helpers.py`

Delete lines 46–58 (the block that introspects mock `.return_value` on `identity_client.verify_jws`):

```python
    legacy_verify = None
    if state.identity_client is not None:
        legacy_verify = getattr(state.identity_client, "verify_jws", None)
    legacy_return = getattr(legacy_verify, "return_value", None)
    if isinstance(legacy_return, dict):
        valid = legacy_return.get("valid")
        if isinstance(valid, bool) and not valid:
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            )
```

After deletion, the function should flow directly from the docstring/setup to the `try:` block that calls `state.platform_agent.validate_certificate(token)`.

### Verification
```bash
cd services/central-bank && just ci-quiet
```
Some tests in `test_review_fixes.py` may now fail — that is expected and will be fixed in Task 4.

---

## Task 2: Remove `verify_jws_path` from config.py

### File: `services/central-bank/src/central_bank_service/config.py`

In the `IdentityConfig` class (around line 63), delete this line:

```python
    verify_jws_path: str | None = None
```

The class should then only have `base_url` and `get_agent_path`.

---

## Task 3: Remove `verify_jws_path` from test configs

### File: `services/central-bank/tests/unit/test_config.py`

Remove the line `verify_jws_path: "/agents/verify-jws"` from the inline YAML strings. There are 2 occurrences (around lines 30 and 74). Delete only the `verify_jws_path` key-value line, keep all surrounding YAML intact.

### File: `services/central-bank/tests/acceptance/run_all.sh`

Remove the line `    verify_jws_path: "/agents/verify-jws"` from the heredoc that writes `bank-config.yaml` (around line 122). Keep the surrounding `identity:` block with `base_url` and `get_agent_path`.

### Verification
```bash
cd services/central-bank && just ci-quiet
```

---

## Task 4: Fix test_review_fixes.py to use PlatformAgent mocks

### File: `services/central-bank/tests/unit/routers/test_review_fixes.py`

The `TestJWSVerificationFailure` class (4 tests) currently sets:
```python
state.identity_client.verify_jws = AsyncMock(return_value={"valid": False, ...})
```

This only worked via the legacy shim we deleted in Task 1. Update these 4 tests to instead configure the PlatformAgent mock to raise `InvalidSignature`:

```python
from cryptography.exceptions import InvalidSignature
from unittest.mock import MagicMock

# Replace the identity_client.verify_jws mock with:
state.platform_agent.validate_certificate = MagicMock(side_effect=InvalidSignature())
```

Each of the 4 tests in `TestJWSVerificationFailure` should:
1. Remove the `state.identity_client.verify_jws = AsyncMock(return_value={"valid": False, ...})` line
2. Add `state.platform_agent.validate_certificate = MagicMock(side_effect=InvalidSignature())`
3. Keep the rest of the test (the HTTP call and assertion) unchanged — they should still expect 403

Also check if there's a `_setup_identity_mock` helper in the same file that sets `identity_client.verify_jws`. The helper itself can stay if it's used by other test classes (it sets up mocks that are inert but harmless). Only the `TestJWSVerificationFailure` class needs updating.

### Verification
```bash
cd services/central-bank && just ci-quiet
```
ALL checks must pass with zero failures.

---

## Final Verification

After all 4 tasks, run from `services/central-bank/`:
```bash
just ci-quiet
```

ALL checks must pass:
- code-format, code-style, code-typecheck, code-lspchecks
- code-security, code-deptry, code-spell, code-semgrep, code-audit
- unit tests (all must pass)

Report the full ci-quiet result.
