# Phase 3 — Core Infrastructure

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/core/state.py`

AppState dataclass following the Central Bank pattern.

### FeedbackRecord Dataclass

The Reputation service defines its own data record — `FeedbackRecord` — co-located with `AppState` in this file (not in schemas.py, because FeedbackRecord is an internal data structure, not a Pydantic response model).

```python
@dataclass
class FeedbackRecord:
    feedback_id: str
    task_id: str
    from_agent_id: str
    to_agent_id: str
    category: str
    rating: str
    comment: str | None
    submitted_at: str
    visible: bool
```

### AppState Fields

```python
@dataclass
class AppState:
    start_time: datetime
    feedback_store: FeedbackStore | None = None
    identity_client: IdentityClient | None = None
```

Use `TYPE_CHECKING` imports for `FeedbackStore` and `IdentityClient` to avoid circular imports.

### Required Functions

Same three functions as Central Bank:
- `get_app_state()` — raises `RuntimeError` if uninitialized
- `init_app_state()` — creates and stores in module-level dict container
- `reset_app_state()` — pops from container (for testing)

### Properties

- `uptime_seconds` — `(now - start_time).total_seconds()`
- `started_at` — ISO 8601 via `start_time.isoformat()`

---

## File 2: `src/reputation_service/core/exceptions.py`

Identical pattern to `central_bank_service/core/exceptions.py`. Three handlers:

1. **`service_error_handler`** — logs warning with error code, status code, and path, returns `{"error", "message", "details"}` JSON
2. **`unhandled_exception_handler`** — logs exception, returns 500 with generic message (no internal details leaked)
3. **`register_exception_handlers(app)`** — delegates to `service_commons.exceptions.register_common_exception_handlers`

Re-export `ServiceError` from `service_commons.exceptions` for convenient imports.

---

## File 3: `src/reputation_service/core/middleware.py`

ASGI middleware for content-type and body size validation. Follow the Central Bank pattern.

### Endpoints Requiring JSON Validation

Only one POST endpoint on the Reputation service:
- `("POST", "/feedback")`

### Behavior

1. Non-HTTP or non-POST/PUT/PATCH requests pass through unchanged
2. POST requests to `/feedback`:
   - Check for duplicate `Content-Type` headers → 400 `BAD_REQUEST` if duplicates found
   - Check `Content-Type` starts with `application/json` → 415 if not
   - Buffer body, check against `max_body_size` → 413 if exceeded
   - Replay buffered body for downstream app

### Error Responses

- 400: `{"error": "BAD_REQUEST", "message": "Duplicate Content-Type header", "details": {}}`
- 415: `{"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Content-Type must be application/json", "details": {}}`
- 413: `{"error": "PAYLOAD_TOO_LARGE", "message": "Request body exceeds maximum allowed size", "details": {}}`

---

## File 4: `src/reputation_service/core/__init__.py`

Export `AppState`, `ServiceError`, `get_app_state`, `init_app_state`. Follow Central Bank pattern.

---

## File 5: `src/reputation_service/core/lifespan.py`

Startup/shutdown lifecycle manager using `@asynccontextmanager`.

### Startup Sequence

1. Load settings via `get_settings()`
2. Setup logging
3. Initialize `AppState` via `init_app_state()`
4. Initialize `FeedbackStore` with `settings.database.path` → `state.feedback_store`
   - The `FeedbackStore` constructor creates the database directory if needed
5. Initialize `IdentityClient` with `settings.identity.base_url`, `settings.identity.verify_jws_path`, `settings.identity.timeout_seconds` → `state.identity_client`
6. Log startup with service name, version, port

### Shutdown Sequence

1. Log shutdown with uptime
2. Close `feedback_store` (SQLite connection) — synchronous `close()`
3. Close `identity_client` — async `close()`, wrapped in try/except for `httpx.HTTPError` and `OSError`

### Key Design Note

The `FeedbackStore` is synchronous (SQLite). The `IdentityClient` is async (`httpx.AsyncClient`). The feedback store operations are fast enough that `run_in_threadpool` is not needed for this service's current scale.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from reputation_service.core.state import AppState; print('state OK')"
uv run python -c "from reputation_service.core.lifespan import lifespan; print('lifespan OK')"
```
