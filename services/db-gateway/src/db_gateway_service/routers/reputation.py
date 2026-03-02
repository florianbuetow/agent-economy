"""Reputation domain endpoints — feedback."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_required_fields,
)

router = APIRouter(prefix="/reputation", tags=["Reputation"])


@router.post("/feedback", status_code=201)
async def submit_feedback(request: Request) -> JSONResponse:
    """Submit feedback for a completed task with optional mutual reveal."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "feedback_id",
            "task_id",
            "from_agent_id",
            "to_agent_id",
            "role",
            "category",
            "rating",
            "submitted_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_feedback(data)
    return JSONResponse(status_code=201, content=result)
