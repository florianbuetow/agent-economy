# Phase 4 — Clients and Storage

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/services/identity_client.py`

Async HTTP client for the Identity service. Follow the Central Bank's `IdentityClient` pattern but with one improvement: configurable timeout via `timeout_seconds` parameter instead of hardcoding.

### Constructor

```python
class IdentityClient:
    def __init__(self, base_url: str, verify_jws_path: str, timeout_seconds: int) -> None:
```

Creates an `httpx.AsyncClient` with `base_url` and `timeout=float(timeout_seconds)`.

### `verify_jws(token: str) -> dict[str, Any]`

Calls `POST {verify_jws_path}` on the Identity service with `{"token": token}`.

**Success path (200 + valid: true):**
- Verify response body is a dict
- Verify `valid` is a boolean and is `True`
- Verify `agent_id` is a string
- Verify `payload` is a dict
- Return the full response body

**Verification failure codes (non-200, known error codes):**
- Define `_VERIFICATION_FAILURE_CODES: ClassVar[set[str]] = {"INVALID_JWS", "AGENT_NOT_FOUND"}`
- If non-200 response has a JSON body with `error` in `_VERIFICATION_FAILURE_CODES`, raise `ServiceError("FORBIDDEN", ..., 403)`
- This maps Identity service validation errors to a single `FORBIDDEN` for the caller

**Invalid signature (200 + valid: false):**
- Raise `ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {})`

**Infrastructure failures:**
- `httpx.HTTPError` (connection refused, timeout) → `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`
- Non-JSON response body → `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`
- Malformed response (missing `valid`, `agent_id`, or `payload`) → `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`
- Unknown non-200 status code → `ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502)`

### `close() -> None`

Closes the underlying `httpx.AsyncClient`.

---

## File 2: `src/reputation_service/services/feedback_store.py`

SQLite-backed feedback storage with thread-safe transactions and atomic mutual reveal.

### Class: `DuplicateFeedbackError`

Domain exception raised when a duplicate `(task_id, from_agent_id, to_agent_id)` is inserted. This prevents leaking `sqlite3.IntegrityError` to callers.

### Class: `FeedbackStore`

#### Constructor

```python
def __init__(self, db_path: str) -> None:
```

- Creates parent directory if needed: `Path(db_path).parent.mkdir(parents=True, exist_ok=True)`
- Opens SQLite connection with `check_same_thread=False`
- Sets `row_factory = sqlite3.Row`
- Configures pragmas: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`
- Creates schema via `_init_schema()`
- Uses `threading.RLock` for thread safety

#### Schema

```sql
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id    TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    from_agent_id  TEXT NOT NULL,
    to_agent_id    TEXT NOT NULL,
    category       TEXT NOT NULL,
    rating         TEXT NOT NULL,
    comment        TEXT,
    submitted_at   TEXT NOT NULL,
    visible        INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback_pair
    ON feedback (task_id, from_agent_id, to_agent_id);

CREATE INDEX IF NOT EXISTS ix_feedback_task
    ON feedback (task_id);

CREATE INDEX IF NOT EXISTS ix_feedback_target_agent
    ON feedback (to_agent_id);
```

#### `insert_feedback(...)` → `FeedbackRecord`

Atomic insert with mutual reveal:

1. Generate `feedback_id = f"fb-{uuid.uuid4()}"`
2. Generate `submitted_at = datetime.now(UTC).isoformat()`
3. `BEGIN IMMEDIATE` transaction
4. Insert the new record (sealed: `visible=0`)
5. Query for reverse pair: same `task_id`, swapped `from_agent_id`/`to_agent_id`
6. If reverse pair exists: `UPDATE feedback SET visible = 1 WHERE feedback_id IN (new, reverse)` — both become visible
7. `COMMIT`
8. On `sqlite3.IntegrityError` with "unique" in message: `ROLLBACK` + raise `DuplicateFeedbackError`
9. On any other exception: `ROLLBACK` + re-raise
10. ROLLBACK uses `contextlib.suppress(sqlite3.Error)` to handle the case where ROLLBACK itself fails

#### Query Methods

- `get_by_id(feedback_id) -> FeedbackRecord | None`
- `get_by_task(task_id) -> list[FeedbackRecord]` — ordered by `submitted_at`
- `get_by_agent(agent_id) -> list[FeedbackRecord]` — where `to_agent_id` matches, ordered by `submitted_at`
- `count() -> int` — total records including sealed

#### `close() -> None`

Closes the SQLite connection.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from reputation_service.services.identity_client import IdentityClient; print('client OK')"
uv run python -c "from reputation_service.services.feedback_store import FeedbackStore; print('store OK')"
```
