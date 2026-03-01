# Phase 7 — Routers

## Working Directory

```
services/court/
```

Routers are thin wrappers. They parse requests, validate JWS tokens, and delegate to the service layer. No business logic lives here.

---

## File 1: `src/court_service/routers/health.py`

Follow the Central Bank pattern.

### `GET /health`

Returns `HealthResponse` with:
- `status: "ok"`
- `uptime_seconds` from `AppState`
- `started_at` from `AppState`
- `total_disputes` — from `dispute_service.count_disputes()`
- `active_disputes` — from `dispute_service.count_active()`

---

## File 2: `src/court_service/routers/disputes.py`

All dispute endpoints in a single router. Five routes total.

### Common JWS Validation Flow (for all POST endpoints)

Every POST endpoint follows the same initial validation sequence. Extract this into a helper function or repeat it in each handler — either way, the order is:

1. Read raw body: `body = await request.body()`
2. Parse JSON: `json.loads(body)` → on failure raise `ServiceError("INVALID_JSON", ..., 400)`
3. Validate it's a dict → on failure raise `ServiceError("INVALID_JSON", "Request body must be a JSON object", 400)`
4. Extract `token` field:
   - Missing or `None` → `ServiceError("INVALID_JWS", "Missing JWS token in request body", 400)`
   - Not a string → `ServiceError("INVALID_JWS", "JWS token must be a string", 400)`
   - Empty string → `ServiceError("INVALID_JWS", "JWS token must not be empty", 400)`
5. Verify via Identity service: `state.identity_client.verify_jws(token)`
   - This may raise `IDENTITY_SERVICE_UNAVAILABLE` (502) or `FORBIDDEN` (403)
6. Check `verified["agent_id"] == settings.platform.agent_id` → raise `ServiceError("FORBIDDEN", ..., 403)` if not
7. Validate `payload["action"]` matches the expected action for this endpoint → raise `ServiceError("INVALID_PAYLOAD", ..., 400)` if not
8. Validate remaining payload fields

### Error Precedence

The validation order above defines the error precedence. This is critical for the auth tests (PREC-01 through PREC-06):

```
415 (Content-Type)          ← handled by middleware
413 (body too large)        ← handled by middleware
400 INVALID_JSON            ← step 2
400 INVALID_JWS             ← step 4
502 IDENTITY_UNAVAILABLE    ← step 5
403 FORBIDDEN (invalid sig) ← step 5 (Identity returns valid: false)
400 INVALID_PAYLOAD         ← step 7
403 FORBIDDEN (not platform)← step 6
409/404 domain errors       ← step 8+
502 downstream errors       ← during side effects
```

**Important**: Step 6 (platform check) happens AFTER step 7 (action validation). This matches the spec's precedence: `INVALID_PAYLOAD` before `FORBIDDEN` for non-platform signer. Re-read the auth spec carefully — the order is: validate action first, then check signer identity. This is because if the action is wrong, the token is clearly not intended for this endpoint regardless of who signed it.

Wait — re-read the auth spec's error precedence section (PREC tests). The spec says:
- PREC-05: action field checked before signer identity → `INVALID_PAYLOAD` beats `FORBIDDEN`

So the correct order after Identity verification is:
1. Validate action field → `INVALID_PAYLOAD` if wrong
2. Check signer is platform → `FORBIDDEN` if not
3. Validate remaining payload fields → `INVALID_PAYLOAD` for missing/invalid fields

### Route: `POST /disputes/file`

Expected action: `"file_dispute"`

Required payload fields: `task_id`, `claimant_id`, `respondent_id`, `claim`, `escrow_id`

Validation:
- All fields present, non-null, non-empty strings
- `claim` length 1–10,000 characters → `ServiceError("INVALID_PAYLOAD", ..., 400)` if violated

Side effects:
1. Fetch task data from Task Board: `state.task_board_client.get_task(task_id)` — may raise `TASK_NOT_FOUND` (404) or `TASK_BOARD_UNAVAILABLE` (502)
2. Create dispute: `state.dispute_service.file_dispute(...)` — may raise `DISPUTE_ALREADY_EXISTS` (409)

Response: 201 with full `DisputeResponse`

### Route: `POST /disputes/{dispute_id}/rebuttal`

Expected action: `"submit_rebuttal"`

Required payload fields: `dispute_id`, `rebuttal`

Validation:
- `payload["dispute_id"]` must match URL path `dispute_id` → `ServiceError("INVALID_PAYLOAD", "Payload dispute_id does not match URL", 400)` if not
- `rebuttal` length 1–10,000 characters

Delegate to: `state.dispute_service.submit_rebuttal(dispute_id, rebuttal)`

Response: 200 with full `DisputeResponse`

### Route: `POST /disputes/{dispute_id}/rule`

Expected action: `"trigger_ruling"`

Required payload fields: `dispute_id`

Validation:
- `payload["dispute_id"]` must match URL path `dispute_id`

Delegate to: `state.dispute_service.execute_ruling(dispute_id, state.judge_panel, task_data, ...)`

Note: The ruling method needs task data. Either fetch it here in the router or have the service fetch it. The spec says the Court fetches task data during filing — so it could be stored on the dispute record. But the API spec shows that task data (spec, deliverables, title, reward) is fetched from Task Board to build the judge context. The simplest approach: fetch task data in the router before calling the service, or pass the task board client to the service and let it fetch.

Response: 200 with full `DisputeResponse` including populated `votes` array

### Route: `GET /disputes/{dispute_id}`

No authentication. Public.

- Fetch dispute: `state.dispute_service.get_dispute(dispute_id)`
- If `None` → raise `ServiceError("DISPUTE_NOT_FOUND", ..., 404)`
- Response: 200 with full `DisputeResponse`

### Route: `GET /disputes`

No authentication. Public.

- Query parameters: `task_id` (optional), `status` (optional)
- Delegate to: `state.dispute_service.list_disputes(task_id, status)`
- Response: 200 with `DisputeListResponse`

### Route Ordering

Place specific routes before parameterized routes to avoid path conflicts:

```python
router = APIRouter()

# POST routes (specific paths first)
@router.post("/disputes/file", status_code=201)
# Then parameterized:
@router.post("/disputes/{dispute_id}/rebuttal")
@router.post("/disputes/{dispute_id}/rule")

# GET routes (list before detail)
@router.get("/disputes")  # Before /{dispute_id}
@router.get("/disputes/{dispute_id}")
```

Actually, since `/disputes/file` is a POST and `/disputes/{dispute_id}` is a GET, there's no conflict between those. But `/disputes/file` (POST) vs `/disputes/{dispute_id}/rebuttal` (POST) — these have different path structures and won't conflict. Just ensure the static path `/disputes/file` is registered before the parameterized paths.

### Method-Not-Allowed Handlers

Add explicit method-not-allowed routes for the test spec (HTTP-01):

```python
@router.api_route("/disputes/file", methods=["GET", "PUT", "PATCH", "DELETE"])
@router.api_route("/disputes", methods=["POST", "PUT", "PATCH", "DELETE"])
```

Check the test spec HTTP-01 for the exact method/route combinations that expect 405.

---

## File 3: `src/court_service/routers/__init__.py`

```python
from court_service.routers import disputes, health

__all__ = ["disputes", "health"]
```

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
```
