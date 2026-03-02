"""Court domain endpoints — claims, rebuttals, rulings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_required_fields,
)

router = APIRouter(prefix="/court", tags=["Court"])


@router.post("/claims", status_code=201)
async def file_claim(request: Request) -> JSONResponse:
    """File a dispute claim."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "claim_id",
            "task_id",
            "claimant_id",
            "respondent_id",
            "reason",
            "status",
            "filed_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.file_claim(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/rebuttals", status_code=201)
async def submit_rebuttal(request: Request) -> JSONResponse:
    """Submit a rebuttal to a dispute claim."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["rebuttal_id", "claim_id", "agent_id", "content", "submitted_at"],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_rebuttal(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/rulings", status_code=201)
async def record_ruling(request: Request) -> JSONResponse:
    """Record a court ruling."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "ruling_id",
            "claim_id",
            "task_id",
            "worker_pct",
            "summary",
            "judge_votes",
            "ruled_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.record_ruling(data)
    return JSONResponse(status_code=201, content=result)
