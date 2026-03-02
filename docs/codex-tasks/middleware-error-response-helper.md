# Extract Middleware Error Responses into Shared Helper

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> After EACH task, run the service's `just test` from the service directory.
> After ALL tasks, run `just ci-all-quiet` from the project root.
> Commit after each task with a descriptive message.

## Background

The endpoint error handling specification (`docs/specifications/endpoint-error-handling.md`) says:

> **Utility helpers** — response objects are created through shared utility functions, not constructed ad-hoc in each endpoint.

> Raise `ServiceError`, never return `JSONResponse` for errors in endpoint code.

All 6 services with ASGI middleware (`core/middleware.py`) construct `JSONResponse` objects inline with hand-built `{"error": ..., "message": ..., "details": ...}` dicts. This is the last remaining violation of the error handling spec.

Middleware runs at the ASGI layer before FastAPI's exception handlers, so it **cannot** raise `ServiceError`. However, the ad-hoc JSONResponse construction can be replaced with a shared helper function from `service-commons` that guarantees the same three-field structure.

### The approach

1. Add a `middleware_error_response()` helper to `libs/service-commons/src/service_commons/exceptions.py`
2. Replace every inline `JSONResponse(status_code=..., content={...})` in middleware files with a call to the helper
3. Tests do not change — the HTTP responses are identical

## Important Rules

1. **Do NOT change `libs/service-commons/` beyond adding the one helper function** — this is a shared library
2. **Do NOT change response content** — the error codes, messages, status codes, and details must stay exactly the same
3. **Do NOT change test files** — the HTTP responses are byte-for-byte identical, tests will pass as-is
4. **Do NOT change any logic** — only replace the JSONResponse construction with the helper call
5. **Run `uv sync --all-extras` in each service directory** after modifying service-commons, since services depend on it via path dependency

## Important Files to Read First

- `AGENTS.md` — project conventions
- `docs/specifications/endpoint-error-handling.md` — the spec this migration enforces
- `libs/service-commons/src/service_commons/exceptions.py` — where the helper goes

---

## Task 1: Add the helper to service-commons

**File:** `libs/service-commons/src/service_commons/exceptions.py`

Add this function after `register_exception_handlers`:

```python
def middleware_error_response(
    error: str,
    message: str,
    status_code: int,
    details: dict[str, object] | None = None,
) -> JSONResponse:
    """Build a standard error JSONResponse for use in ASGI middleware.

    Middleware runs before FastAPI exception handlers, so it cannot raise
    ``ServiceError``.  This helper ensures middleware error responses use
    the same three-field structure as all other error responses.
    """
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "message": message,
            "details": details if details is not None else {},
        },
    )
```

**No tests needed for this function** — it is a trivial wrapper around JSONResponse and will be exercised by every existing middleware test.

### Verification

```bash
cd libs/service-commons && uv run python -c "from service_commons.exceptions import middleware_error_response; print('OK')"
```

### Commit message

```
feat(service-commons): add middleware_error_response helper
```

---

## Task 2: Identity Service

**Service directory:** `services/identity/`

**File:** `src/identity_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 415 JSONResponse (lines 51-58) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
3. Replace the 413 JSONResponse (lines 73-80) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

Run `uv sync --all-extras` first to pick up the service-commons change.

### Verification

```bash
cd services/identity && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(identity): use middleware_error_response helper in middleware
```

---

## Task 3: Central Bank Service

**Service directory:** `services/central-bank/`

**File:** `src/central_bank_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 415 JSONResponse (lines 68-75) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
3. Replace the 413 JSONResponse (lines 90-97) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

### Verification

```bash
cd services/central-bank && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(central-bank): use middleware_error_response helper in middleware
```

---

## Task 4: Task Board Service

**Service directory:** `services/task-board/`

**File:** `src/task_board_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 415 JSONResponse for multipart (lines 82-88) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be multipart/form-data",
       status_code=415,
   )
   ```
3. Replace the 415 JSONResponse for JSON (lines 100-107) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
4. Replace the 413 JSONResponse (lines 122-129) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

### Verification

```bash
cd services/task-board && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(task-board): use middleware_error_response helper in middleware
```

---

## Task 5: Reputation Service

**Service directory:** `services/reputation/`

**File:** `src/reputation_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 400 JSONResponse for duplicate Content-Type (lines 52-58) with:
   ```python
   response = middleware_error_response(
       error="bad_request",
       message="Duplicate Content-Type header",
       status_code=400,
   )
   ```
3. Replace the 415 JSONResponse (lines 65-72) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
4. Replace the 413 JSONResponse (lines 87-94) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

### Verification

```bash
cd services/reputation && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(reputation): use middleware_error_response helper in middleware
```

---

## Task 6: Court Service

**Service directory:** `services/court/`

**File:** `src/court_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 415 JSONResponse (lines 60-67) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
3. Replace the 413 JSONResponse (lines 81-88) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

### Verification

```bash
cd services/court && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(court): use middleware_error_response helper in middleware
```

---

## Task 7: DB Gateway Service

**Service directory:** `services/db-gateway/`

**File:** `src/db_gateway_service/core/middleware.py`

1. Replace the import:
   - Remove: `from fastapi.responses import JSONResponse`
   - Add: `from service_commons.exceptions import middleware_error_response`
2. Replace the 415 JSONResponse (lines 42-49) with:
   ```python
   response = middleware_error_response(
       error="unsupported_media_type",
       message="Content-Type must be application/json",
       status_code=415,
   )
   ```
3. Replace the 413 JSONResponse (lines 64-71) with:
   ```python
   response = middleware_error_response(
       error="payload_too_large",
       message="Request body exceeds maximum allowed size",
       status_code=413,
   )
   ```

### Verification

```bash
cd services/db-gateway && uv sync --all-extras && just test && just ci-quiet
```

### Commit message

```
refactor(db-gateway): use middleware_error_response helper in middleware
```

---

## Task 8: Final Verification

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

All 8 services must pass all CI checks. If any fail, fix them before stopping.

---

## Summary Table

| Service | Instances | Error Codes |
|---------|-----------|-------------|
| identity | 2 | unsupported_media_type (415), payload_too_large (413) |
| central-bank | 2 | unsupported_media_type (415), payload_too_large (413) |
| task-board | 3 | unsupported_media_type (415) x2, payload_too_large (413) |
| reputation | 3 | bad_request (400), unsupported_media_type (415), payload_too_large (413) |
| court | 2 | unsupported_media_type (415), payload_too_large (413) |
| db-gateway | 2 | unsupported_media_type (415), payload_too_large (413) |
| **Total** | **14** | |

Note: The original audit counted 15 but the task-board multipart 415 and JSON 415 are in the same file — 14 individual JSONResponse constructions across 6 files, plus 1 new helper function in service-commons.
