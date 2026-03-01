"""Demo proxy endpoints for human agent interactions.

These endpoints accept plain JSON from the frontend, sign JWS tokens
via DemoSigner, and forward requests to the Task Board service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from observatory_service.config import get_settings
from observatory_service.core.state import get_app_state
from observatory_service.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


async def _parse_json(request: Request) -> dict[str, Any]:
    """Parse JSON body from request, raising ServiceError on failure."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise ServiceError(
            error="INVALID_JSON",
            message="Request body must be valid JSON",
            status_code=400,
            details=None,
        ) from exc
    return body


def _require_field(body: dict[str, Any], field: str) -> Any:
    """Require a field in the parsed JSON body."""
    if field not in body:
        raise ServiceError(
            error="MISSING_FIELD",
            message=f"Missing required field: {field}",
            status_code=400,
            details={"field": field},
        )
    return body[field]


@router.post("/demo/tasks")
async def create_task(request: Request) -> JSONResponse:
    """Create a task on behalf of the demo human agent."""
    body = await _parse_json(request)

    title: str = _require_field(body, "title")
    spec: str = _require_field(body, "spec")
    reward: int = _require_field(body, "reward")

    state = get_app_state()
    if state.demo_signer is None or state.task_board_client is None:
        raise ServiceError(
            error="DEMO_NOT_AVAILABLE",
            message="Demo proxy is not initialized",
            status_code=503,
            details=None,
        )

    settings = get_settings()
    task_board_url = settings.demo.task_board_url

    tokens = state.demo_signer.sign_create_task(title=title, spec=spec, reward=reward)

    url = f"{task_board_url}/tasks"
    payload = {
        "task_token": tokens["task_token"],
        "escrow_token": tokens["escrow_token"],
    }

    resp = await state.task_board_client.post(url, json=payload)

    if not resp.is_success:
        logger.error(
            "task_board_create_task_failed",
            extra={"status_code": resp.status_code, "body": resp.text},
        )
        raise ServiceError(
            error="UPSTREAM_ERROR",
            message="Task Board returned an error",
            status_code=502,
            details={"upstream_status": resp.status_code},
        )

    upstream_data: dict[str, Any] = resp.json()
    return JSONResponse(content=upstream_data, status_code=201)


@router.post("/demo/tasks/{task_id}/accept-bid")
async def accept_bid(request: Request, task_id: str) -> JSONResponse:
    """Accept a bid on behalf of the demo human agent."""
    body = await _parse_json(request)

    bid_id: str = _require_field(body, "bid_id")

    state = get_app_state()
    if state.demo_signer is None or state.task_board_client is None:
        raise ServiceError(
            error="DEMO_NOT_AVAILABLE",
            message="Demo proxy is not initialized",
            status_code=503,
            details=None,
        )

    settings = get_settings()
    task_board_url = settings.demo.task_board_url

    token = state.demo_signer.sign_accept_bid(task_id=task_id, bid_id=bid_id)

    url = f"{task_board_url}/tasks/{task_id}/bids/{bid_id}/accept"
    payload = {"token": token}

    resp = await state.task_board_client.post(url, json=payload)

    if not resp.is_success:
        logger.error(
            "task_board_accept_bid_failed",
            extra={"status_code": resp.status_code, "body": resp.text},
        )
        raise ServiceError(
            error="UPSTREAM_ERROR",
            message="Task Board returned an error",
            status_code=502,
            details={"upstream_status": resp.status_code},
        )

    upstream_data: dict[str, Any] = resp.json()
    return JSONResponse(content=upstream_data, status_code=200)


@router.post("/demo/tasks/{task_id}/dispute")
async def dispute_task(request: Request, task_id: str) -> JSONResponse:
    """File a dispute on behalf of the demo human agent."""
    body = await _parse_json(request)

    reason: str = _require_field(body, "reason")

    state = get_app_state()
    if state.demo_signer is None or state.task_board_client is None:
        raise ServiceError(
            error="DEMO_NOT_AVAILABLE",
            message="Demo proxy is not initialized",
            status_code=503,
            details=None,
        )

    settings = get_settings()
    task_board_url = settings.demo.task_board_url

    token = state.demo_signer.sign_dispute(task_id=task_id, reason=reason)

    url = f"{task_board_url}/tasks/{task_id}/dispute"
    payload = {"token": token}

    resp = await state.task_board_client.post(url, json=payload)

    if not resp.is_success:
        logger.error(
            "task_board_dispute_failed",
            extra={"status_code": resp.status_code, "body": resp.text},
        )
        raise ServiceError(
            error="UPSTREAM_ERROR",
            message="Task Board returned an error",
            status_code=502,
            details={"upstream_status": resp.status_code},
        )

    upstream_data: dict[str, Any] = resp.json()
    return JSONResponse(content=upstream_data, status_code=200)
