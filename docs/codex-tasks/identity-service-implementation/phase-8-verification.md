# Phase 8 — Full Verification

## Working Directory

All commands run from `services/identity/`.

---

## Step 8.1: Run all CI checks

```bash
cd services/identity && just ci-quiet
```

All checks must pass:
- Code formatting (ruff format)
- Code style (ruff check)
- Type checking (mypy)
- Security (bandit)
- Dependency hygiene (deptry)
- Spelling (codespell)
- Static analysis (semgrep)
- Vulnerability scan (pip-audit)
- Unit tests (pytest)
- Strict type checking (pyright)

---

## Step 8.2: Start the service

```bash
cd services/identity && just run
```

Wait for `Uvicorn running on http://0.0.0.0:8001` in the output.

---

## Step 8.3: Run acceptance tests

In a **separate terminal**, run each test:

```bash
cd services/identity && bash tests/acceptance/test-reg-01.sh
```

To run ALL acceptance tests at once:

```bash
cd services/identity
for f in tests/acceptance/test-*.sh; do
    echo "=== Running $f ==="
    bash "$f"
    if [ $? -ne 0 ]; then
        echo "FAILED: $f"
        exit 1
    fi
done
echo "ALL TESTS PASSED"
```

All 48 acceptance tests must pass.

---

## Step 8.4: Stop the service

```bash
cd services/identity && just kill
```

---

## Troubleshooting Guide

### Problem: 415 on valid POST requests

**Cause**: Middleware rejecting because Content-Type header is missing or wrong.
**Check**: Ensure the middleware only checks POST/PUT/PATCH methods and checks `content_type.startswith("application/json")`.

### Problem: 404 instead of 405 on GET /agents/register

**Cause**: Route ordering — `GET /agents/{agent_id}` matches before the explicit reject route.
**Fix**: Ensure all `/agents/register` and `/agents/verify` routes (including the `api_route` reject handlers) are defined BEFORE `GET /agents/{agent_id}` in `routers/agents.py`.

### Problem: 422 instead of 400 on validation errors

**Cause**: Using Pydantic model binding in the endpoint signature instead of manual JSON parsing.
**Fix**: Endpoints must use `request: Request` + `await request.body()` + `json.loads()` for body parsing, NOT `data: SomeModel` in the function signature.

### Problem: 405 returns `{"detail": "Method Not Allowed"}` instead of error envelope

**Cause**: The `http_exception_handler` for StarletteHTTPException is not registered.
**Fix**: Check that `register_exception_handlers` in `core/exceptions.py` calls `app.add_exception_handler(StarletteHTTPException, ...)`.

### Problem: VER-14 fails with 413

**Cause**: `max_body_size` is too small. 1 MB payload encodes to ~1.4 MB JSON.
**Fix**: `max_body_size` must be at least 1572864 (1.5 MB).

### Problem: REG-18 passes instead of returning 413

**Cause**: `max_body_size` is too large.
**Fix**: Must be less than ~2 MB. Value of 1572864 is correct.

### Problem: REG-14 (all-zero key) passes registration

**Cause**: `Ed25519PublicKey.from_public_bytes` may accept all-zero bytes on some platforms.
**Fix**: Explicit check `if key_bytes == b"\x00" * public_key_bytes` before calling `from_public_bytes`.

### Problem: Tests pass individually but fail with `just test-unit`

**Cause**: Global state leaking between tests (AppState or settings cache).
**Fix**: Ensure `tests/unit/conftest.py` has the autouse fixture that calls `clear_settings_cache()` and `reset_app_state()` both before and after each test.

### Problem: mypy or pyright errors

**Typical fixes**:
- Add `from __future__ import annotations` at the top of the file
- Move imports to `TYPE_CHECKING` block for forward references
- Add `# type: ignore[...]` comments for ASGI middleware type mismatches (e.g., `buffered_receive`)
- Ensure all function signatures have return type annotations

### Problem: semgrep "no default values" rule triggers

**Fix**: The `create_settings_loader` line should have `# nosemgrep` comment. No other line should have default values for configurable settings. `AppState` defaults (like `registry: AgentRegistry | None = None`) are runtime state, not configuration — they should not trigger the rule.

### Problem: ruff ERA rule (eradicate) flags code

**Cause**: Commented-out code in source files.
**Fix**: Remove all comments that look like code. Use actual English comments only.
