"""Bid submission, listing, and acceptance endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from task_board_service.core.state import get_app_state
from task_board_service.routers.validation import (
    extract_bearer_token,
    extract_token,
    parse_json_body,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /tasks/{task_id}/bids — submit bid
# MUST be before GET /tasks/{task_id}/bids
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/bids", status_code=201)
async def submit_bid(task_id: str, request: Request) -> JSONResponse:
    """Submit a bid on a task."""
    body = await request.body()
    data = parse_json_body(body)
    token = extract_token(data, "token")

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
    token = extract_bearer_token(authorization, required=False)

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
    data = parse_json_body(body)
    token = extract_token(data, "token")

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
    _ = (task_id, bid_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
