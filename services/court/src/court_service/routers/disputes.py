"""Dispute endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from court_service.config import get_settings
from court_service.core.state import get_app_state
from court_service.routers.validation import (
    extract_jws_token,
    parse_json_body,
    require_action,
    require_non_empty_string,
    require_platform_signer,
    verify_jws,
)

router = APIRouter()


@router.post("/disputes/file", status_code=201)
async def file_dispute(request: Request) -> JSONResponse:
    """File a new dispute (platform-signed)."""
    settings = get_settings()
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)
    if state.task_board_client is None:
        msg = "Task Board client not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    verified_payload = await verify_jws(token, state.identity_client)
    signer_agent_id = require_non_empty_string(verified_payload, "agent_id")
    payload = verified_payload.get("payload")
    if not isinstance(payload, dict):
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Identity service returned malformed verification response",
            502,
            {},
        )

    require_action(payload, "file_dispute")
    require_platform_signer({"agent_id": signer_agent_id}, settings.platform.agent_id)

    task_id = require_non_empty_string(payload, "task_id")
    claimant_id = require_non_empty_string(payload, "claimant_id")
    respondent_id = require_non_empty_string(payload, "respondent_id")
    claim = require_non_empty_string(payload, "claim")
    escrow_id = require_non_empty_string(payload, "escrow_id")

    if len(claim) > settings.disputes.max_claim_length:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Claim exceeds maximum length",
            400,
            {},
        )

    try:
        await state.task_board_client.get_task(task_id)
    except ServiceError as exc:
        if exc.error == "TASK_NOT_FOUND":
            raise
        if exc.error == "TASK_BOARD_UNAVAILABLE" or exc.status_code >= 500:
            raise ServiceError(
                "TASK_BOARD_UNAVAILABLE",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc
        raise
    except Exception as exc:
        raise ServiceError(
            "TASK_BOARD_UNAVAILABLE",
            "Cannot reach Task Board service",
            502,
            {},
        ) from exc

    created = await run_in_threadpool(
        state.dispute_service.file_dispute,
        task_id,
        claimant_id,
        respondent_id,
        claim,
        escrow_id,
        settings.disputes.rebuttal_deadline_seconds,
    )
    return JSONResponse(status_code=201, content=created)


@router.post("/disputes/{dispute_id}/rebuttal")
async def submit_rebuttal(dispute_id: str, request: Request) -> JSONResponse:
    """Submit rebuttal for a dispute (platform-signed)."""
    settings = get_settings()
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    verified_payload = await verify_jws(token, state.identity_client)
    signer_agent_id = require_non_empty_string(verified_payload, "agent_id")
    payload = verified_payload.get("payload")
    if not isinstance(payload, dict):
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Identity service returned malformed verification response",
            502,
            {},
        )

    require_action(payload, "submit_rebuttal")
    require_platform_signer({"agent_id": signer_agent_id}, settings.platform.agent_id)

    payload_dispute_id = require_non_empty_string(payload, "dispute_id")
    if payload_dispute_id != dispute_id:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Payload dispute_id does not match URL",
            400,
            {},
        )

    rebuttal = require_non_empty_string(payload, "rebuttal")
    if len(rebuttal) > settings.disputes.max_rebuttal_length:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Rebuttal exceeds maximum length",
            400,
            {},
        )

    updated = await run_in_threadpool(state.dispute_service.submit_rebuttal, dispute_id, rebuttal)
    return JSONResponse(status_code=200, content=updated)


@router.post("/disputes/{dispute_id}/rule")
async def trigger_ruling(dispute_id: str, request: Request) -> JSONResponse:
    """Trigger dispute ruling (platform-signed)."""
    settings = get_settings()
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)
    if state.task_board_client is None:
        msg = "Task Board client not initialized"
        raise RuntimeError(msg)
    if state.central_bank_client is None:
        msg = "Central Bank client not initialized"
        raise RuntimeError(msg)
    if state.reputation_client is None:
        msg = "Reputation client not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    verified_payload = await verify_jws(token, state.identity_client)
    signer_agent_id = require_non_empty_string(verified_payload, "agent_id")
    payload = verified_payload.get("payload")
    if not isinstance(payload, dict):
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Identity service returned malformed verification response",
            502,
            {},
        )

    require_action(payload, "trigger_ruling")
    require_platform_signer({"agent_id": signer_agent_id}, settings.platform.agent_id)

    payload_dispute_id = require_non_empty_string(payload, "dispute_id")
    if payload_dispute_id != dispute_id:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Payload dispute_id does not match URL",
            400,
            {},
        )

    dispute = await run_in_threadpool(state.dispute_service.get_dispute, dispute_id)
    if dispute is None:
        raise ServiceError("DISPUTE_NOT_FOUND", "Dispute not found", 404, {})

    task_id = str(dispute["task_id"])
    try:
        task_data = await state.task_board_client.get_task(task_id)
    except ServiceError as exc:
        if exc.error == "TASK_BOARD_UNAVAILABLE" or exc.status_code >= 500:
            raise ServiceError(
                "TASK_BOARD_UNAVAILABLE",
                "Cannot reach Task Board service",
                502,
                {},
            ) from exc
        raise ServiceError(
            "TASK_BOARD_UNAVAILABLE",
            "Cannot fetch task context from Task Board",
            502,
            {},
        ) from exc
    except Exception as exc:
        raise ServiceError(
            "TASK_BOARD_UNAVAILABLE",
            "Cannot reach Task Board service",
            502,
            {},
        ) from exc

    judges = state.judges if state.judges is not None else []
    ruled = await state.dispute_service.execute_ruling(
        dispute_id=dispute_id,
        judges=judges,
        task_data=task_data,
        task_board_client=state.task_board_client,
        central_bank_client=state.central_bank_client,
        reputation_client=state.reputation_client,
        platform_agent_id=settings.platform.agent_id,
    )
    return JSONResponse(status_code=200, content=ruled)


@router.api_route("/disputes/file", methods=["GET", "PUT", "PATCH", "DELETE"])
async def file_dispute_method_not_allowed(_request: Request) -> None:
    """Reject unsupported methods for file-dispute endpoint."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.get("/disputes/{dispute_id}")
async def get_dispute(dispute_id: str) -> JSONResponse:
    """Fetch full dispute details."""
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)

    dispute = await run_in_threadpool(state.dispute_service.get_dispute, dispute_id)
    if dispute is None:
        raise ServiceError("DISPUTE_NOT_FOUND", "Dispute not found", 404, {})
    return JSONResponse(status_code=200, content=dispute)


@router.get("/disputes")
async def list_disputes(request: Request) -> JSONResponse:
    """List dispute summaries with optional filters."""
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)

    task_id = request.query_params.get("task_id")
    status = request.query_params.get("status")
    disputes = await run_in_threadpool(state.dispute_service.list_disputes, task_id, status)
    return JSONResponse(status_code=200, content={"disputes": disputes})


@router.api_route("/disputes", methods=["POST", "PUT", "PATCH", "DELETE"])
async def disputes_collection_method_not_allowed(_request: Request) -> None:
    """Reject unsupported methods for disputes collection endpoint."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
