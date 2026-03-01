# Phase 6 — Routers

## Working Directory

All paths relative to `services/task-board/`.

---

## File 1: `src/task_board_service/routers/health.py`

Create this file:

```python
"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from task_board_service.core.state import get_app_state
from task_board_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    total_tasks = 0
    tasks_by_status: dict[str, int] = {}
    if state.task_manager is not None:
        stats = state.task_manager.get_stats()
        total_tasks = stats["total_tasks"]
        tasks_by_status = stats["tasks_by_status"]
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_tasks=total_tasks,
        tasks_by_status=tasks_by_status,
    )
```

---

## File 2: `src/task_board_service/routers/tasks.py`

Create this file. This is the longest router file — it handles 8 endpoints across the task lifecycle.

**CRITICAL — Route order matters.** All specific action routes (`/tasks`, `/tasks/{task_id}/cancel`, etc.) MUST be defined BEFORE the parameterized `GET /tasks/{task_id}` route, otherwise requests to `/tasks/{task_id}/cancel` could match the wrong handler.

```python
"""Task lifecycle endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: JSON body parsing and token extraction
# ---------------------------------------------------------------------------


def _parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def _extract_token(data: dict[str, Any], field_name: str) -> str:
    """Extract and validate a token field from parsed JSON body.

    Validates that the field exists, is not None, is a string, and is not empty.
    Raises INVALID_JWS on any failure.
    """
    if field_name not in data:
        raise ServiceError(
            "INVALID_JWS",
            f"Missing required field: {field_name}",
            400,
            {},
        )

    value = data[field_name]

    if value is None:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be null",
            400,
            {},
        )

    if not isinstance(value, str):
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must be a string",
            400,
            {},
        )

    if not value:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be empty",
            400,
            {},
        )

    return value


# ---------------------------------------------------------------------------
# POST /tasks — create task (MUST be before GET /tasks/{task_id})
# ---------------------------------------------------------------------------


@router.post("/tasks", status_code=201)
async def create_task(request: Request) -> JSONResponse:
    """Create a new task with escrow."""
    body = await request.body()
    data = _parse_json_body(body)

    # Extract and validate both tokens before calling service
    task_token = _extract_token(data, "task_token")
    escrow_token = _extract_token(data, "escrow_token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.create_task(task_token, escrow_token)
    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# GET /tasks — list tasks (MUST be before GET /tasks/{task_id})
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def list_tasks(
    status: str | None = None,
    poster_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """List tasks with optional filters."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    tasks = await state.task_manager.list_tasks(
        status=status,
        poster_id=poster_id,
        worker_id=worker_id,
    )
    return {"tasks": tasks}


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/cancel
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> JSONResponse:
    """Cancel a task and release escrow to the poster."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.cancel_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/submit
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/submit")
async def submit_deliverable(task_id: str, request: Request) -> JSONResponse:
    """Submit deliverables for review."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.submit_deliverable(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/approve
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, request: Request) -> JSONResponse:
    """Approve deliverables and release payment to the worker."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.approve_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/dispute
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/dispute")
async def dispute_task(task_id: str, request: Request) -> JSONResponse:
    """Dispute deliverables and send to Court for resolution."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.dispute_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/ruling
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/ruling")
async def record_ruling(task_id: str, request: Request) -> JSONResponse:
    """Record a Court ruling (platform-signed operation)."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.record_ruling(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Method-not-allowed: action routes
#
# Without these, requests like GET /tasks/{task_id}/cancel would match
# GET /tasks/{task_id} with task_id="xxx/cancel" — which may return
# TASK_NOT_FOUND instead of the required 405.
# ---------------------------------------------------------------------------


@router.api_route(
    "/tasks/{task_id}/cancel",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def cancel_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/cancel."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/submit",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def submit_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/submit."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/approve",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def approve_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/approve."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/dispute",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def dispute_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/dispute."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/ruling",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def ruling_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/ruling."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


# ---------------------------------------------------------------------------
# GET /tasks/{task_id} — MUST be LAST (parameterized catch-all)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    """Get full task details."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    return await state.task_manager.get_task(task_id)
```

**Design decisions explained:**

1. **Manual JSON parsing** instead of Pydantic model binding: The acceptance tests expect `400 INVALID_JSON` and `400 INVALID_JWS`, but FastAPI's Pydantic validation returns `422`. Manual parsing gives exact control over error codes.

2. **`_extract_token` helper**: Validates that a token field exists, is not `None`, is a string, and is not empty. All four checks raise `INVALID_JWS` per the auth spec error precedence (step 4). Used for the `"token"` field on most endpoints and for both `"task_token"` and `"escrow_token"` on `POST /tasks`.

3. **Route ordering**: All specific action routes (`/cancel`, `/submit`, `/approve`, `/dispute`, `/ruling`) and the collection endpoints (`POST /tasks`, `GET /tasks`) are defined before the parameterized `GET /tasks/{task_id}`. FastAPI matches routes top-to-bottom.

4. **Explicit method-not-allowed routes**: Without these, `GET /tasks/{task_id}/cancel` could match `GET /tasks/{task_id}` with `task_id="xxx/cancel"`, returning `TASK_NOT_FOUND` instead of `405`. The `api_route` handlers for unsupported methods raise `ServiceError` to produce the standard error format.

5. **Other 405 cases** (`DELETE /tasks/{id}`, `POST /health`, `PUT /tasks`, etc.) are handled automatically by Starlette's router (returns 405 when path matches but method doesn't), formatted by our `http_exception_handler` in `core/exceptions.py`.

6. **JSONResponse wrapping**: `POST /tasks` returns `JSONResponse(status_code=201, ...)` explicitly because the service layer returns a plain `dict`. All other POST endpoints return `JSONResponse(status_code=200, ...)` for consistency. `GET` endpoints return `dict` directly — FastAPI auto-serializes to JSON with status 200.

7. **`await state.task_manager.create_task(...)`**: All service methods are `async` because they call external services (Identity, Central Bank) via `httpx`. The routers `await` every call.

---

## File 3: `src/task_board_service/routers/bids.py`

Create this file:

```python
"""Bid submission, listing, and acceptance endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: JSON body parsing and token extraction (shared with tasks.py)
# ---------------------------------------------------------------------------


def _parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def _extract_token(data: dict[str, Any], field_name: str) -> str:
    """Extract and validate a token field from parsed JSON body.

    Validates that the field exists, is not None, is a string, and is not empty.
    Raises INVALID_JWS on any failure.
    """
    if field_name not in data:
        raise ServiceError(
            "INVALID_JWS",
            f"Missing required field: {field_name}",
            400,
            {},
        )

    value = data[field_name]

    if value is None:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be null",
            400,
            {},
        )

    if not isinstance(value, str):
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must be a string",
            400,
            {},
        )

    if not value:
        raise ServiceError(
            "INVALID_JWS",
            f"Field '{field_name}' must not be empty",
            400,
            {},
        )

    return value


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract JWS token from Authorization header.

    Returns the token string if a valid Bearer header is present,
    or None if no Authorization header exists.
    Raises INVALID_JWS if the header exists but is malformed.
    """
    if authorization is None:
        return None

    if not authorization.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Authorization header must use Bearer scheme",
            400,
            {},
        )

    token = authorization[len("Bearer "):]
    if not token:
        raise ServiceError(
            "INVALID_JWS",
            "Bearer token must not be empty",
            400,
            {},
        )

    return token


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/bids — submit bid
# MUST be before GET /tasks/{task_id}/bids
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/bids", status_code=201)
async def submit_bid(task_id: str, request: Request) -> JSONResponse:
    """Submit a bid on a task."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.submit_bid(task_id, token)
    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/bids — list bids (conditional auth)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/bids")
async def list_bids(task_id: str, request: Request) -> dict[str, Any]:
    """List bids for a task. Sealed during OPEN phase (requires poster auth)."""
    # Extract optional Authorization header
    authorization = request.headers.get("authorization")
    token = _extract_bearer_token(authorization)

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    return await state.task_manager.list_bids(task_id, token)


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/bids/{bid_id}/accept — accept bid
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/bids/{bid_id}/accept")
async def accept_bid(task_id: str, bid_id: str, request: Request) -> JSONResponse:
    """Accept a bid, assign worker, start execution deadline."""
    body = await request.body()
    data = _parse_json_body(body)
    token = _extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.accept_bid(task_id, bid_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Method-not-allowed: bid routes
# ---------------------------------------------------------------------------


@router.api_route(
    "/tasks/{task_id}/bids/{bid_id}/accept",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def accept_method_not_allowed(
    task_id: str,
    bid_id: str,
    request: Request,
) -> None:
    """Reject wrong methods on /tasks/{task_id}/bids/{bid_id}/accept."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
```

**Design decisions explained:**

1. **`_parse_json_body` and `_extract_token` are duplicated** across `tasks.py` and `bids.py` (and not extracted to a shared module). This is intentional — router files are thin and self-contained. The functions are short (under 30 lines each), and extracting them to a shared module would create a dependency between router files. Each router file can be understood and tested independently.

2. **`_extract_bearer_token` helper**: For `GET /tasks/{task_id}/bids`, the JWS token is in the `Authorization: Bearer <token>` header (not the JSON body). This helper extracts it. It returns `None` if no header is present (because the endpoint is public when the task is NOT in OPEN status). The service layer decides whether auth is actually required based on the task's current status.

3. **Bearer token validation**: If the `Authorization` header exists but does not start with `"Bearer "` or has an empty token after the prefix, the router raises `INVALID_JWS` immediately. This is router-level structural validation — the service layer never sees a malformed header.

4. **Conditional auth flow**: The router always passes the token (or `None`) to `state.task_manager.list_bids(task_id, token)`. The service layer checks the task status: if OPEN, it requires the token and verifies the signer is the poster. If not OPEN, it ignores the token and returns bids publicly.

5. **Route ordering**: `POST /tasks/{task_id}/bids` and `GET /tasks/{task_id}/bids` are both registered on the same path but different methods — Starlette handles this correctly. `POST /tasks/{task_id}/bids/{bid_id}/accept` is registered before its method-not-allowed handler.

6. **405 for accept**: The explicit `api_route` handler prevents `GET /tasks/{task_id}/bids/{bid_id}/accept` from being caught by `GET /tasks/{task_id}/bids` with `bid_id` absorbed into some other routing artifact.

---

## File 4: `src/task_board_service/routers/assets.py`

Create this file:

```python
"""Asset upload, listing, and download endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: Bearer token extraction from Authorization header
# ---------------------------------------------------------------------------


def _extract_bearer_token(authorization: str | None) -> str:
    """Extract JWS token from Authorization header.

    Unlike the bids router version, this always requires the token.
    Raises INVALID_JWS if the header is missing, malformed, or empty.
    """
    if authorization is None:
        raise ServiceError(
            "INVALID_JWS",
            "Missing Authorization header",
            400,
            {},
        )

    if not authorization.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Authorization header must use Bearer scheme",
            400,
            {},
        )

    token = authorization[len("Bearer "):]
    if not token:
        raise ServiceError(
            "INVALID_JWS",
            "Bearer token must not be empty",
            400,
            {},
        )

    return token


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/assets — upload asset
# MUST be before GET /tasks/{task_id}/assets/{asset_id}
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/assets", status_code=201)
async def upload_asset(task_id: str, request: Request) -> JSONResponse:
    """Upload a deliverable asset (multipart/form-data)."""
    # Extract auth token from Authorization header
    authorization = request.headers.get("authorization")
    token = _extract_bearer_token(authorization)

    # Parse multipart form data
    form = await request.form()
    upload_file = form.get("file")

    if upload_file is None:
        raise ServiceError(
            "NO_FILE",
            "No file part in the multipart request",
            400,
            {},
        )

    # Read file content and metadata
    content = await upload_file.read()  # type: ignore[union-attr]
    filename = upload_file.filename or "unnamed"  # type: ignore[union-attr]
    content_type = upload_file.content_type or "application/octet-stream"  # type: ignore[union-attr]

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.upload_asset(
        task_id,
        token,
        content,
        filename,
        content_type,
    )
    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets — list assets (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets")
async def list_assets(task_id: str) -> dict[str, Any]:
    """List all assets for a task."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    return await state.task_manager.list_assets(task_id)


# ---------------------------------------------------------------------------
# GET /tasks/{task_id}/assets/{asset_id} — download asset (public)
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/assets/{asset_id}")
async def download_asset(task_id: str, asset_id: str) -> Response:
    """Download an asset file."""
    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    content, content_type, filename = await state.task_manager.download_asset(
        task_id,
        asset_id,
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
```

**Design decisions explained:**

1. **`_extract_bearer_token` in assets is stricter** than in bids. The bids version returns `None` when no header is present (because `GET /bids` is conditionally authenticated). The assets version always requires the header and raises `INVALID_JWS` if missing. Asset upload always requires authentication.

2. **Multipart parsing**: Uses `await request.form()` (provided by `python-multipart`). The `"file"` field is extracted. If no file is present, raises `NO_FILE` per the API spec. The `# type: ignore[union-attr]` comments are necessary because `form.get("file")` returns `str | UploadFile | None`, and mypy/pyright cannot narrow it after the `None` check (the `str` branch is not relevant for file uploads but is part of the type signature).

3. **File metadata**: `filename` defaults to `"unnamed"` if the client does not provide one. `content_type` defaults to `"application/octet-stream"`. These defaults match standard HTTP upload conventions.

4. **Download response**: Returns a raw `Response` (not `JSONResponse`) with the file content, the original `Content-Type`, and a `Content-Disposition: attachment` header with the original filename. This prompts the browser/client to download the file.

5. **Route ordering**: `POST /tasks/{task_id}/assets` and `GET /tasks/{task_id}/assets` share the same path. `GET /tasks/{task_id}/assets/{asset_id}` is a deeper path and does not conflict. No explicit method-not-allowed handlers are needed here — Starlette handles 405 automatically for paths where both GET and POST exist.

6. **Content-Type enforcement**: The middleware (`RequestValidationMiddleware`) already validates that `POST` requests to `/assets` paths have `multipart/form-data` Content-Type. The router does not re-check.

---

## File 5: `src/task_board_service/routers/__init__.py`

Overwrite the existing empty file with:

```python
"""API routers."""

from task_board_service.routers import assets, bids, health, tasks

__all__ = ["assets", "bids", "health", "tasks"]
```

---

## Service Layer Contract

The routers depend on `TaskManager` methods with these signatures. These methods are implemented in Phase 5 (`services/task_manager.py`). Listed here for reference so the router code is understandable:

```python
class TaskManager:
    # Task lifecycle
    async def create_task(self, task_token: str, escrow_token: str) -> dict[str, Any]: ...
    async def get_task(self, task_id: str) -> dict[str, Any]: ...
    async def list_tasks(self, *, status: str | None, poster_id: str | None, worker_id: str | None) -> list[dict[str, Any]]: ...
    async def cancel_task(self, task_id: str, token: str) -> dict[str, Any]: ...
    async def submit_deliverable(self, task_id: str, token: str) -> dict[str, Any]: ...
    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]: ...
    async def dispute_task(self, task_id: str, token: str) -> dict[str, Any]: ...
    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]: ...

    # Bids
    async def submit_bid(self, task_id: str, token: str) -> dict[str, Any]: ...
    async def list_bids(self, task_id: str, token: str | None) -> dict[str, Any]: ...
    async def accept_bid(self, task_id: str, bid_id: str, token: str) -> dict[str, Any]: ...

    # Assets
    async def upload_asset(self, task_id: str, token: str, content: bytes, filename: str, content_type: str) -> dict[str, Any]: ...
    async def list_assets(self, task_id: str) -> dict[str, Any]: ...
    async def download_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]: ...

    # Stats
    def get_stats(self) -> dict[str, Any]: ...
```

All service methods return plain `dict` objects. The routers are responsible for wrapping them in `JSONResponse` with the appropriate status code (201 for creates, 200 for everything else) or returning them directly (for GET endpoints where FastAPI handles serialization).

---

## Verification

```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.

**Note:** This phase depends on `task_board_service.core.state`, `task_board_service.schemas`, and `service_commons.exceptions` existing (imported in the router files). If Phases 2 and 3 have not been completed, the ruff check will still pass (ruff does not resolve imports), but running the service will fail. Complete Phases 2-5 first.
