"""Board domain endpoints — tasks, bids, task status, assets."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_constraints,
    validate_event,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_required_fields,
)

router = APIRouter(prefix="/board", tags=["Board"])


@router.post("/tasks", status_code=201)
async def create_task(request: Request) -> JSONResponse:
    """Create a new task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "status",
            "bidding_deadline_seconds",
            "deadline_seconds",
            "review_deadline_seconds",
            "bidding_deadline",
            "escrow_id",
            "created_at",
        ],
    )
    validate_event(data)
    validate_positive_integer(data, "reward")
    if "bid_count" not in data:
        data["bid_count"] = 0
    else:
        validate_non_negative_integer(data, "bid_count")
    if "escrow_pending" not in data:
        data["escrow_pending"] = 0
    else:
        validate_non_negative_integer(data, "escrow_pending")

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.create_task(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/bids", status_code=201)
async def submit_bid(request: Request) -> JSONResponse:
    """Submit a bid on a task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["bid_id", "task_id", "bidder_id", "proposal", "submitted_at"],
    )
    if "amount" not in data:
        data["amount"] = 0
    else:
        validate_positive_integer(data, "amount")
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.submit_bid(data, constraints)
    return JSONResponse(status_code=201, content=result)


@router.post("/tasks/{task_id}/status")
async def update_task_status(task_id: str, request: Request) -> JSONResponse:
    """Update a task's status and associated fields."""
    body = await request.body()
    data = parse_json_body(body)

    # Validate updates object
    updates = data.get("updates")
    if updates is None:
        raise ServiceError(
            "missing_field",
            "Missing required field: updates",
            400,
            {"field": "updates"},
        )
    if not isinstance(updates, dict):
        raise ServiceError(
            "missing_field",
            "Field 'updates' must be an object",
            400,
            {"field": "updates"},
        )
    if len(updates) == 0:
        raise ServiceError(
            "empty_updates",
            "updates object contains no fields",
            400,
            {},
        )
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.update_task_status(task_id, data, constraints)
    return JSONResponse(status_code=200, content=result)


@router.post("/assets", status_code=201)
async def record_asset(request: Request) -> JSONResponse:
    """Record an asset upload (metadata only)."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "asset_id",
            "task_id",
            "uploader_id",
            "filename",
            "content_type",
            "size_bytes",
            "storage_path",
            "uploaded_at",
        ],
    )
    constraints = validate_constraints(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.record_asset(data, constraints)
    return JSONResponse(status_code=201, content=result)


@router.get("/tasks/count")
async def count_tasks() -> JSONResponse:
    """Count total tasks."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_tasks()
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/tasks/count-by-status")
async def count_tasks_by_status() -> JSONResponse:
    """Count tasks grouped by status."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    counts = state.db_reader.count_tasks_by_status()
    return JSONResponse(status_code=200, content=counts)


@router.get("/tasks")
async def list_tasks(request: Request) -> JSONResponse:
    """List tasks with optional status/poster/worker filters."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="method_not_allowed",
            message="Method not allowed",
            status_code=405,
            details={},
        )

    params = request.query_params
    status = params.get("status")
    poster_id = params.get("poster_id")
    worker_id = params.get("worker_id")
    limit_str = params.get("limit")
    offset_str = params.get("offset")
    limit = int(limit_str) if limit_str is not None else None
    offset = int(offset_str) if offset_str is not None else None
    tasks = state.db_reader.list_tasks(status, poster_id, worker_id, limit, offset)
    return JSONResponse(status_code=200, content={"tasks": tasks})


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> JSONResponse:
    """Get a task by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task = state.db_reader.get_task(task_id)
    if task is None:
        raise ServiceError(
            error="task_not_found",
            message="Task not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=task)


@router.get("/tasks/{task_id}/bids")
async def get_bids_for_task(task_id: str) -> JSONResponse:
    """Get bids for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    bids = state.db_reader.get_bids_for_task(task_id)
    return JSONResponse(status_code=200, content={"bids": bids})


@router.get("/tasks/{task_id}/assets/count")
async def count_assets(task_id: str) -> JSONResponse:
    """Count assets for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_assets(task_id)
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/tasks/{task_id}/assets")
async def get_assets_for_task(task_id: str) -> JSONResponse:
    """Get assets for a task."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    assets = state.db_reader.get_assets_for_task(task_id)
    return JSONResponse(status_code=200, content={"assets": assets})


@router.get("/bids/{bid_id}")
async def get_bid(bid_id: str, request: Request) -> JSONResponse:
    """Get a bid by ID with required task_id query parameter."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task_id = request.query_params.get("task_id", "")
    bid = state.db_reader.get_bid(bid_id, task_id)
    if bid is None:
        raise ServiceError(
            error="bid_not_found",
            message="Bid not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=bid)


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> JSONResponse:
    """Get an asset by ID with required task_id query parameter."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    task_id = request.query_params.get("task_id", "")
    asset = state.db_reader.get_asset(asset_id, task_id)
    if asset is None:
        raise ServiceError(
            error="asset_not_found",
            message="Asset not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=asset)
