"""Court domain endpoints — claims, rebuttals, rulings."""

from __future__ import annotations

from typing import Any

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
from db_gateway_service.services.court_writer import ACKNOWLEDGMENT_STEPS, CourtRulingWriter

router = APIRouter(prefix="/court", tags=["Court"])


def _ruling_writer() -> CourtRulingWriter:
    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )
    return CourtRulingWriter(state.db_writer)


def _strict_int(data: dict[str, Any], field: str, minimum: int, maximum: int) -> int:
    value = data.get(field)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ServiceError(
            "invalid_field",
            f"{field} must be an integer in [{minimum}, {maximum}]",
            400,
            {"field": field},
        )
    return value


@router.post("/claims/{claim_id}/judging-lease")
async def acquire_judging_lease(claim_id: str, request: Request) -> JSONResponse:
    """Claim (or reclaim an expired) judging lease for one Court ruling run."""
    data = parse_json_body(await request.body())
    validate_required_fields(data, ["lease_id", "lease_seconds"])
    lease_seconds = _strict_int(data, "lease_seconds", 1, 86400)
    result = _ruling_writer().acquire_judging_lease(claim_id, str(data["lease_id"]), lease_seconds)
    return JSONResponse(status_code=200, content=result)


@router.post("/claims/{claim_id}/judging-lease/release")
async def release_judging_lease(claim_id: str, request: Request) -> JSONResponse:
    """Release the run's lease so a retry can resume immediately."""
    data = parse_json_body(await request.body())
    validate_required_fields(data, ["lease_id"])
    result = _ruling_writer().release_judging_lease(claim_id, str(data["lease_id"]))
    return JSONResponse(status_code=200, content=result)


@router.post("/claims/{claim_id}/decision", status_code=201)
async def claim_decision(claim_id: str, request: Request) -> JSONResponse:
    """Persist the judged decision before any delivery (compare-and-set)."""
    data = parse_json_body(await request.body())
    validate_required_fields(data, ["lease_id", "worker_pct", "summary", "judge_votes", "request"])
    _strict_int(data, "worker_pct", 0, 100)
    if not isinstance(data["request"], dict):
        raise ServiceError(
            "invalid_field", "request must be a JSON object", 400, {"field": "request"}
        )
    if not isinstance(data["summary"], str) or not isinstance(data["judge_votes"], str):
        raise ServiceError("invalid_field", "summary and judge_votes must be strings", 400, {})
    result = _ruling_writer().claim_decision(claim_id, data)
    return JSONResponse(status_code=201, content=result)


@router.post("/claims/{claim_id}/acknowledgments")
async def record_acknowledgment(claim_id: str, request: Request) -> JSONResponse:
    """Record one delivered acknowledgment of the stored decision."""
    data = parse_json_body(await request.body())
    validate_required_fields(data, ["lease_id", "step"])
    step = data["step"]
    if step not in ACKNOWLEDGMENT_STEPS:
        raise ServiceError(
            "invalid_field",
            "step must be one of: " + ", ".join(sorted(ACKNOWLEDGMENT_STEPS)),
            400,
            {"field": "step"},
        )
    result = _ruling_writer().record_acknowledgment(claim_id, str(data["lease_id"]), str(step))
    return JSONResponse(status_code=200, content=result)


@router.post("/claims/{claim_id}/ruled")
async def complete_ruling(claim_id: str, request: Request) -> JSONResponse:
    """Mark the claim ruled once every delivery is acknowledged."""
    data = parse_json_body(await request.body())
    validate_required_fields(data, ["lease_id"])
    result = _ruling_writer().complete_ruling(claim_id, str(data["lease_id"]))
    return JSONResponse(status_code=200, content=result)


@router.get("/claims/{claim_id}/ruling-progress")
async def ruling_progress(claim_id: str) -> JSONResponse:
    """Return the stored decision, request snapshot and acknowledgments."""
    return JSONResponse(status_code=200, content=_ruling_writer().ruling_progress(claim_id))


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
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
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
    """Update claim status with optional constraints."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["status"])
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
async def delete_ruling(claim_id: str, request: Request) -> JSONResponse:
    """Delete a ruling by claim_id."""
    body = await request.body()
    data = parse_json_body(body)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.delete_ruling(claim_id, data["event"])
    return JSONResponse(status_code=200, content=result)
