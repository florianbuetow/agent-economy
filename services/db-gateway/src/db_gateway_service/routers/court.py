"""Court domain endpoints — claims, rebuttals, rulings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_constraints,
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
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

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

    result = state.db_writer.submit_rebuttal(data, constraints)
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
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.record_ruling(data)
    return JSONResponse(status_code=201, content=result)


@router.get("/claims/count")
async def count_claims() -> JSONResponse:
    """Count total court claims."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )
    return JSONResponse(status_code=200, content={"count": state.db_reader.count_claims()})


@router.get("/claims/count-active")
async def count_active_claims() -> JSONResponse:
    """Count unresolved court claims."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )
    return JSONResponse(
        status_code=200,
        content={"count": state.db_reader.count_active_claims()},
    )


@router.get("/claims")
async def list_claims(request: Request) -> JSONResponse:
    """List claims with optional status and claimant_id filters."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="method_not_allowed",
            message="Method not allowed",
            status_code=405,
            details={},
        )
    status = request.query_params.get("status")
    claimant_id = request.query_params.get("claimant_id")
    claims = state.db_reader.list_claims(status, claimant_id)
    return JSONResponse(status_code=200, content={"claims": claims})


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str) -> JSONResponse:
    """Get a claim by claim_id."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )
    claim = state.db_reader.get_claim(claim_id)
    if claim is None:
        raise ServiceError(
            error="claim_not_found",
            message="Claim not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=claim)


@router.post("/claims/{claim_id}/status")
async def update_claim_status(claim_id: str, request: Request) -> JSONResponse:
    """Update claim status with optional constraints and event."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["status"])
    constraints = validate_constraints(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.update_claim_status(claim_id, data, constraints)
    return JSONResponse(status_code=200, content=result)


@router.get("/claims/{claim_id}/rebuttal")
async def get_rebuttal(claim_id: str) -> JSONResponse:
    """Get rebuttal by claim_id."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )
    rebuttal = state.db_reader.get_rebuttal(claim_id)
    if rebuttal is None:
        raise ServiceError(
            error="rebuttal_not_found",
            message="No rebuttal for this claim",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=rebuttal)


@router.get("/rulings/{claim_id}")
async def get_ruling(claim_id: str) -> JSONResponse:
    """Get ruling by claim_id."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )
    ruling = state.db_reader.get_ruling(claim_id)
    if ruling is None:
        raise ServiceError(
            error="ruling_not_found",
            message="Ruling not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=ruling)


@router.delete("/rulings/{claim_id}")
async def delete_ruling(claim_id: str) -> JSONResponse:
    """Delete a ruling by claim_id."""
    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.delete_ruling(claim_id)
    return JSONResponse(status_code=200, content=result)
