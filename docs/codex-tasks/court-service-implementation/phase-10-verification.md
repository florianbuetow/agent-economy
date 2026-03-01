# Phase 10 — Full Verification

## Working Directory

```
services/court/
```

No code to write in this phase. This is the final validation gate.

---

## Step 10.1: Run Full CI Pipeline

```bash
just ci-quiet
```

This runs ALL checks:
- `ruff format --check` — formatting
- `ruff check` — linting/style
- `mypy` — type checking
- `pyright` — strict type checking
- `bandit` — security scanning
- `deptry` — dependency checking
- `codespell` — spell checking
- `semgrep` — custom rules
- `pip-audit` — vulnerability scanning
- `pytest` — all tests (unit, integration, performance)

**The service is NOT complete until `just ci-quiet` passes with zero failures.**

---

## Step 10.2: Smoke Test the Running Service

Start the service:

```bash
just run
```

In another terminal, test these endpoints:

```bash
# Health check
curl -s http://localhost:8005/health | python3 -m json.tool

# List disputes (empty)
curl -s http://localhost:8005/disputes | python3 -m json.tool

# Get non-existent dispute (404)
curl -s http://localhost:8005/disputes/disp-nonexistent | python3 -m json.tool

# Wrong method (405)
curl -s -X DELETE http://localhost:8005/disputes/file | python3 -m json.tool

# Wrong content type (415)
curl -s -X POST http://localhost:8005/disputes/file -H "Content-Type: text/plain" -d "hello" | python3 -m json.tool
```

Stop the service when done.

---

## Troubleshooting Guide

### Problem: Import errors on startup

**Cause**: Missing `__init__.py` or incorrect package structure.
**Fix**: Verify all `__init__.py` files exist in: `core/`, `routers/`, `services/`, `judges/`.

### Problem: Config validation fails at startup

**Cause**: `config.yaml` missing required sections or invalid values.
**Fix**: Check `judges.panel_size` is odd, `platform.agent_id` is non-empty, `platform.private_key_path` points to a real file.

### Problem: PlatformSigner fails to load key

**Cause**: `private_key_path` doesn't exist or isn't a valid Ed25519 key.
**Fix**: Generate a key (see Phase 8 troubleshooting) and update `config.yaml`.

### Problem: 404 instead of 405 on wrong HTTP method

**Cause**: Missing explicit method-not-allowed route handlers.
**Fix**: Add `@router.api_route(path, methods=[...])` handlers that raise `ServiceError("METHOD_NOT_ALLOWED", ..., 405)`. Check which method/route combinations the test spec (HTTP-01) expects.

### Problem: PREC tests fail (wrong error precedence)

**Cause**: Validation steps in the router are in the wrong order.
**Fix**: Re-read Phase 7's error precedence section. The order is: Content-Type (middleware) → body size (middleware) → JSON parse → JWS token extraction → Identity verification → action validation → platform check → domain errors.

### Problem: Ruling rollback tests fail

**Cause**: Dispute status not reverting to `rebuttal_pending` after failed side effects.
**Fix**: Ensure the `execute_ruling` method wraps steps 6–10 in try/except and reverts status on any `ServiceError`.

### Problem: `JUDGE_UNAVAILABLE` not raised on LLM failure

**Cause**: LiteLLM exceptions not caught and mapped.
**Fix**: Wrap `litellm.acompletion` in try/except that catches `Exception` broadly and raises `ServiceError("JUDGE_UNAVAILABLE", ..., 502)`.

### Problem: mypy/pyright type errors on mock setup in tests

**Cause**: `AsyncMock` assignments to typed attributes.
**Fix**: Use `# type: ignore[assignment]` or ensure the mock's return type matches the expected signature.

### Problem: `codespell` flags a word in prompts or error messages

**Cause**: Technical terms or domain jargon not in codespell's dictionary.
**Fix**: Add the word to `config/codespell/ignore-words.txt`.

### Problem: `semgrep` flags a pattern

**Cause**: Custom rules in `config/semgrep/` matching something in the court service.
**Fix**: Read the semgrep rule to understand what it's checking, then fix the code to comply. Use `# nosemgrep` only as a last resort with a comment explaining why.
