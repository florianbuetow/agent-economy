"""Board domain endpoints — tasks, bids, task status, assets."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
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

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

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
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_bid(data)
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
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.update_task_status(task_id, data)
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
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.record_asset(data)
    return JSONResponse(status_code=201, content=result)
