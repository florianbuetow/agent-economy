# Phase 3 — Core Infrastructure

## Working Directory

```
services/court/
```

---

## File 1: `src/court_service/core/state.py`

AppState dataclass following the Central Bank pattern. The Court service has more clients than any other service.

### AppState Fields

```python
@dataclass
class AppState:
    start_time: datetime
    dispute_service: DisputeService | None = None
    identity_client: IdentityClient | None = None
    platform_signer: PlatformSigner | None = None
    task_board_client: TaskBoardClient | None = None
    central_bank_client: CentralBankClient | None = None
    reputation_client: ReputationClient | None = None
    judge_panel: list[Judge] | None = None
```

Use `TYPE_CHECKING` imports for all client types to avoid circular imports.

### Required Functions

Same three functions as Central Bank:
- `get_app_state()` — raises `RuntimeError` if uninitialized
- `init_app_state()` — creates and stores in module-level dict container
- `reset_app_state()` — sets to `None` (for testing)

### Properties

- `uptime_seconds` — `(now - start_time).total_seconds()`
- `started_at` — ISO 8601 with `Z` suffix (same format as Central Bank)

---

## File 2: `src/court_service/core/exceptions.py`

Identical pattern to `central_bank_service/core/exceptions.py`. Three handlers:

1. **`service_error_handler`** — logs warning, returns `{"error", "message", "details"}` JSON
2. **`unhandled_exception_handler`** — logs exception, returns 500 with generic message (no internal details leaked)
3. **`http_exception_handler`** — handles 405 from Starlette router, returns `METHOD_NOT_ALLOWED`

Wire them via `register_exception_handlers(app)` which delegates to `service_commons.exceptions.register_common_exception_handlers` and adds the Starlette handler.

---

## File 3: `src/court_service/core/middleware.py`

ASGI middleware for content-type and body size validation. Follow the Central Bank pattern exactly.

### Endpoints Requiring JSON Validation

All POST endpoints on the Court service:
- `("POST", "/disputes/file")`
- Prefix match: `("POST", "/disputes/")` — catches `/disputes/{id}/rebuttal` and `/disputes/{id}/rule`

### Behavior

1. Non-HTTP or non-POST requests pass through unchanged
2. POST requests to matching endpoints:
   - Check `Content-Type` starts with `application/json` → 415 if not
   - Buffer body, check against `max_body_size` → 413 if exceeded
   - Replay buffered body for downstream app

### Error Responses

- 415: `{"error": "UNSUPPORTED_MEDIA_TYPE", "message": "Content-Type must be application/json", "details": {}}`
- 413: `{"error": "PAYLOAD_TOO_LARGE", "message": "Request body exceeds maximum allowed size", "details": {}}`

---

## File 4: `src/court_service/core/__init__.py`

Export `AppState`, `ServiceError`, `get_app_state`, `init_app_state`. Follow Central Bank pattern.

---

## File 5: `src/court_service/core/lifespan.py`

Startup/shutdown lifecycle manager using `@asynccontextmanager`.

### Startup Sequence

1. Load settings via `get_settings()`
2. Setup logging
3. Initialize `AppState` via `init_app_state()`
4. Ensure database directory exists (`Path(db_path).parent.mkdir(parents=True, exist_ok=True)`)
5. Initialize `DisputeService` with `db_path` → `state.dispute_service`
6. Initialize `IdentityClient` with `identity.base_url`, `identity.verify_jws_path` → `state.identity_client`
7. Initialize `PlatformSigner` from `platform.private_key_path` and `platform.agent_id` → `state.platform_signer`
8. Initialize `TaskBoardClient` with `task_board.base_url` → `state.task_board_client`
9. Initialize `CentralBankClient` with `central_bank.base_url` → `state.central_bank_client`
10. Initialize `ReputationClient` with `reputation.base_url` → `state.reputation_client`
11. Build judge panel from `judges.judges` config — create one `LLMJudge` per configured judge → `state.judge_panel`
12. Validate panel size: `len(state.judge_panel)` must equal `settings.judges.panel_size` (defense-in-depth; config validator should have caught this already)
13. Log startup

### Shutdown Sequence

1. Log shutdown with uptime
2. Close all async HTTP clients: `identity_client`, `task_board_client`, `central_bank_client`, `reputation_client`
3. Close `dispute_service` (SQLite connection)

### Key Design Note

The `PlatformSigner` is synchronous (Ed25519 key loaded once at startup). All HTTP clients are async (`httpx.AsyncClient`). The `DisputeService` uses SQLite which is synchronous — router calls should use `starlette.concurrency.run_in_threadpool` for blocking operations.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from court_service.core.state import AppState; print('state OK')"
uv run python -c "from court_service.core.lifespan import lifespan; print('lifespan OK')"
```
