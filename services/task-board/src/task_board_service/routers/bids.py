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

    token = authorization[len("Bearer ") :]
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
    _ = (task_id, bid_id, request)
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
