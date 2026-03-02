"""Dispute endpoints."""

from __future__ import annotations

from typing import Any

import httpx
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
    verify_platform_token,
)

router = APIRouter()


async def _fetch_task(task_id: str) -> dict[str, Any]:
    """Fetch task data from Task Board via the platform agent."""
    state = get_app_state()
    if state.platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)
    try:
        result: dict[str, Any] = await state.platform_agent.get_task(task_id)
        return result
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ServiceError("task_not_found", "Task not found", 404, {}) from exc
        raise ServiceError(
            "task_board_unavailable",
            "Cannot reach Task Board service",
            502,
            {},
        ) from exc
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError(
            "task_board_unavailable",
            "Cannot reach Task Board service",
            502,
            {},
        ) from exc


@router.post("/disputes/file", status_code=201)
async def file_dispute(request: Request) -> JSONResponse:
    """File a new dispute (platform-signed)."""
    settings = get_settings()
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)
    if state.platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    payload = verify_platform_token(token, state.platform_agent)

    require_action(payload, "file_dispute")

    task_id = require_non_empty_string(payload, "task_id")
    claimant_id = require_non_empty_string(payload, "claimant_id")
    respondent_id = require_non_empty_string(payload, "respondent_id")
    claim = require_non_empty_string(payload, "claim")
    escrow_id = require_non_empty_string(payload, "escrow_id")

    if len(claim) > settings.disputes.max_claim_length:
        raise ServiceError(
            "invalid_payload",
            "Claim exceeds maximum length",
            400,
            {},
        )

    await _fetch_task(task_id)

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
    if state.platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    payload = verify_platform_token(token, state.platform_agent)

    require_action(payload, "submit_rebuttal")

    payload_dispute_id = require_non_empty_string(payload, "dispute_id")
    if payload_dispute_id != dispute_id:
        raise ServiceError(
            "invalid_payload",
            "Payload dispute_id does not match URL",
            400,
            {},
        )

    rebuttal = require_non_empty_string(payload, "rebuttal")
    if len(rebuttal) > settings.disputes.max_rebuttal_length:
        raise ServiceError(
            "invalid_payload",
            "Rebuttal exceeds maximum length",
            400,
            {},
        )

    updated = await run_in_threadpool(state.dispute_service.submit_rebuttal, dispute_id, rebuttal)
    return JSONResponse(status_code=200, content=updated)


@router.post("/disputes/{dispute_id}/rule")
async def trigger_ruling(dispute_id: str, request: Request) -> JSONResponse:
    """Trigger dispute ruling (platform-signed)."""
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)
    if state.platform_agent is None:
        msg = "Platform agent not initialized"
        raise RuntimeError(msg)

    data = parse_json_body(await request.body())
    token = extract_jws_token(data, field="token")
    payload = verify_platform_token(token, state.platform_agent)

    require_action(payload, "trigger_ruling")

    payload_dispute_id = require_non_empty_string(payload, "dispute_id")
    if payload_dispute_id != dispute_id:
        raise ServiceError(
            "invalid_payload",
            "Payload dispute_id does not match URL",
            400,
            {},
        )

    dispute = await run_in_threadpool(state.dispute_service.get_dispute, dispute_id)
    if dispute is None:
        raise ServiceError("dispute_not_found", "Dispute not found", 404, {})

    task_id = str(dispute["task_id"])
    task_data = await _fetch_task(task_id)

    judges = state.judges if state.judges is not None else []
    ruled = await state.dispute_service.execute_ruling(
        dispute_id=dispute_id,
        judges=judges,
        task_data=task_data,
        platform_agent=state.platform_agent,
    )
    return JSONResponse(status_code=200, content=ruled)


@router.api_route("/disputes/file", methods=["GET", "PUT", "PATCH", "DELETE"])
async def file_dispute_method_not_allowed(_request: Request) -> None:
    """Reject unsupported methods for file-dispute endpoint."""
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})


@router.get("/disputes/{dispute_id}")
async def get_dispute(dispute_id: str) -> JSONResponse:
    """Fetch full dispute details."""
    state = get_app_state()
    if state.dispute_service is None:
        msg = "Dispute service not initialized"
        raise RuntimeError(msg)

    dispute = await run_in_threadpool(state.dispute_service.get_dispute, dispute_id)
    if dispute is None:
        raise ServiceError("dispute_not_found", "Dispute not found", 404, {})
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
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})
