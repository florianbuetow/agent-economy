"""Task lifecycle endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from task_board_service.routers.validation import extract_token, parse_json_body

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /tasks — create task (MUST be before GET /tasks/{task_id})
# ---------------------------------------------------------------------------


@router.post("/tasks", status_code=201)
async def create_task(request: Request) -> JSONResponse:
    """Create a new task with escrow."""
    body = await request.body()
    data = {} if body == b"" else parse_json_body(body)

    # Extract and validate both tokens before calling service
    task_token = extract_token(data, "task_token")
    escrow_token = extract_token(data, "escrow_token")

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
async def list_tasks(request: Request) -> dict[str, Any]:
    """List tasks with optional filters."""
    status = request.query_params.get("status")
    poster_id = request.query_params.get("poster_id")
    worker_id = request.query_params.get("worker_id")
    offset_raw = request.query_params.get("offset")
    limit_raw = request.query_params.get("limit")

    offset: int | None = None
    limit: int | None = None

    if offset_raw is not None:
        try:
            offset = int(offset_raw)
        except ValueError as exc:
            raise ServiceError("INVALID_PAYLOAD", "offset must be an integer", 400, {}) from exc
        if offset < 0:
            raise ServiceError("INVALID_PAYLOAD", "offset must be >= 0", 400, {})

    if limit_raw is not None:
        try:
            limit = int(limit_raw)
        except ValueError as exc:
            raise ServiceError("INVALID_PAYLOAD", "limit must be an integer", 400, {}) from exc
        if limit <= 0:
            raise ServiceError("INVALID_PAYLOAD", "limit must be >= 1", 400, {})

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    tasks = await state.task_manager.list_tasks(
        status=status,
        poster_id=poster_id,
        worker_id=worker_id,
        offset=offset,
        limit=limit,
    )
    return {"tasks": tasks}


# ---------------------------------------------------------------------------
# Cancel endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request) -> JSONResponse:
    """Cancel a task and release escrow to the poster."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.cancel_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Submit endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/submit")
async def submit_deliverable(task_id: str, request: Request) -> JSONResponse:
    """Submit deliverables for review."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.submit_deliverable(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Approve endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str, request: Request) -> JSONResponse:
    """Approve deliverables and release payment to the worker."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.approve_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Dispute endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/dispute")
async def dispute_task(task_id: str, request: Request) -> JSONResponse:
    """Dispute deliverables and send to Court for resolution."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

    state = get_app_state()
    if state.task_manager is None:
        msg = "TaskManager not initialized"
        raise RuntimeError(msg)

    result = await state.task_manager.dispute_task(task_id, token)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# Ruling endpoint
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/ruling")
async def record_ruling(task_id: str, request: Request) -> JSONResponse:
    """Record a Court ruling (platform-signed operation)."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

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
    _ = (task_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/submit",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def submit_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/submit."""
    _ = (task_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/approve",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def approve_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/approve."""
    _ = (task_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/dispute",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def dispute_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/dispute."""
    _ = (task_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/tasks/{task_id}/ruling",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def ruling_method_not_allowed(task_id: str, request: Request) -> None:
    """Reject wrong methods on /tasks/{task_id}/ruling."""
    _ = (task_id, request)
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
