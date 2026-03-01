# Phase 6 — Routers

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/routers/health.py`

Health check endpoint. Same pattern as Central Bank.

```python
@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
```

Returns:
- `status: "ok"`
- `uptime_seconds` from AppState
- `started_at` from AppState
- `total_feedback` from `feedback_store.count()` — counts ALL records including sealed

If `feedback_store` is `None`, raise `ServiceError("SERVICE_UNAVAILABLE", ..., 503)`.

---

## File 2: `src/reputation_service/routers/feedback.py`

All feedback endpoints. This is the largest router file.

### Helper: `_extract_jws_token(data: dict) -> str`

Extracts and validates the `token` field from the parsed request body:

1. `token` not in data or `None` → raise `ServiceError("INVALID_JWS", ..., 400)`
2. `token` is not a string → raise `ServiceError("INVALID_JWS", ..., 400)`
3. `token` is empty string → raise `ServiceError("INVALID_JWS", ..., 400)`
4. `token.split(".")` does not produce exactly 3 parts → raise `ServiceError("INVALID_JWS", ..., 400)`
5. Return the token string

### Helper: `_record_to_dict(record: FeedbackRecord) -> dict`

Converts a `FeedbackRecord` dataclass to a plain dict for JSON serialization.

### `POST /feedback` — `submit_feedback_endpoint`

Authentication flow (JWS-wrapped requests only):

1. Parse JSON body manually (`json.loads(await request.body())`)
   - `JSONDecodeError` or `UnicodeDecodeError` → `ServiceError("INVALID_JSON", ..., 400)`
   - Not a dict → `ServiceError("INVALID_JSON", ..., 400)`
2. Extract JWS token via `_extract_jws_token(data)`
3. Verify JWS via `state.identity_client.verify_jws(token)` — returns `{valid, agent_id, payload}`
   - `FORBIDDEN` or `IDENTITY_SERVICE_UNAVAILABLE` propagate as-is from IdentityClient
4. Validate payload `action` == `"submit_feedback"` → `ServiceError("INVALID_PAYLOAD", ..., 400)` if not
5. Validate `from_agent_id` is present and a string in payload → `ServiceError("INVALID_PAYLOAD", ..., 400)` if not
6. Check signer matches: `verified["agent_id"] != payload["from_agent_id"]` → `ServiceError("FORBIDDEN", ..., 403)`
7. Extract feedback fields from payload (strip `action` key)
8. Call `submit_feedback(store, feedback_body, max_comment_length)`
9. If `ValidationError` result → raise `ServiceError(...)` with the validation error details
10. Return `201` with the `FeedbackRecord` as JSON

### `GET /feedback/task/{task_id}` — `get_task_feedback`

1. Call `get_feedback_for_task(store, task_id, reveal_timeout_seconds)`
2. Return `200` with `{"task_id": task_id, "feedback": [...]}`
3. Empty list if no visible feedback (including unknown task_id)

### `GET /feedback/agent/{agent_id}` — `get_agent_feedback`

1. Call `get_feedback_for_agent(store, agent_id, reveal_timeout_seconds)`
2. Return `200` with `{"agent_id": agent_id, "feedback": [...]}`
3. Empty list if no visible feedback

### `GET /feedback/{feedback_id}` — `get_feedback`

1. Call `get_feedback_by_id(store, feedback_id, reveal_timeout_seconds)`
2. If `None` → raise `ServiceError("FEEDBACK_NOT_FOUND", ..., 404)`
3. Return `200` with the feedback record

### Route Registration Order

**Critical:** The `/feedback/task/{task_id}` and `/feedback/agent/{agent_id}` routes MUST be registered before `/feedback/{feedback_id}`. Otherwise, FastAPI will match `task` and `agent` as `feedback_id` values.

```python
router = APIRouter()
# POST first
@router.post("/feedback")
# Specific GET paths before generic
@router.get("/feedback/task/{task_id}")
@router.get("/feedback/agent/{agent_id}")
# Generic last
@router.get("/feedback/{feedback_id}")
```

---

## File 3: `src/reputation_service/routers/__init__.py`

Export `feedback` and `health` routers:

```python
from reputation_service.routers import feedback, health
```

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from reputation_service.routers.feedback import router; print('router OK')"
```
