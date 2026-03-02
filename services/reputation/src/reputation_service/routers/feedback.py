"""Feedback endpoints."""

from __future__ import annotations

import base64
import json

from cryptography.exceptions import InvalidSignature
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from reputation_service.config import get_settings
from reputation_service.core.exceptions import ServiceError
from reputation_service.core.state import FeedbackRecord, get_app_state
from reputation_service.services.feedback import (
    ValidationError,
    get_feedback_by_id,
    get_feedback_for_agent,
    get_feedback_for_task,
    submit_feedback,
)

router = APIRouter()


def _extract_jws_token(data: dict[str, object]) -> str:
    """Extract and validate the JWS token from the request body."""
    if "token" not in data or data["token"] is None:
        raise ServiceError(
            error="invalid_jws",
            message="Missing JWS token in request body",
            status_code=400,
            details={},
        )

    if not isinstance(data["token"], str):
        raise ServiceError(
            error="invalid_jws",
            message="JWS token must be a string",
            status_code=400,
            details={},
        )

    token: str = data["token"]

    if token == "":  # nosec B105
        raise ServiceError(
            error="invalid_jws",
            message="JWS token must not be empty",
            status_code=400,
            details={},
        )

    parts = token.split(".")
    if len(parts) != 3:
        raise ServiceError(
            error="invalid_jws",
            message="JWS token must be a three-part compact serialization",
            status_code=400,
            details={},
        )

    return token


def _extract_signer_agent_id(token: str) -> str:
    """Extract signer agent_id from the JWS header kid field."""
    header_b64 = token.split(".", maxsplit=1)[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(padded))
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        raise ServiceError(
            error="invalid_jws",
            message="JWS header is not valid base64url JSON",
            status_code=400,
            details={},
        ) from exc
    if not isinstance(header, dict):
        raise ServiceError(
            error="invalid_jws",
            message="JWS header must be a JSON object",
            status_code=400,
            details={},
        )
    kid = header.get("kid")
    if not isinstance(kid, str) or kid == "":
        raise ServiceError(
            error="invalid_jws",
            message="Token header is missing kid",
            status_code=400,
            details={},
        )
    return kid


def _record_to_dict(record: object) -> dict[str, object]:
    """Convert a FeedbackRecord dataclass to a dict for JSON serialization."""
    if not isinstance(record, FeedbackRecord):
        raise TypeError("Expected FeedbackRecord")
    return {
        "feedback_id": record.feedback_id,
        "task_id": record.task_id,
        "from_agent_id": record.from_agent_id,
        "to_agent_id": record.to_agent_id,
        "category": record.category,
        "rating": record.rating,
        "comment": record.comment,
        "submitted_at": record.submitted_at,
        "visible": record.visible,
    }


@router.post("/feedback")
async def submit_feedback_endpoint(request: Request) -> JSONResponse:
    """Submit feedback for a completed task."""
    settings = get_settings()

    # Parse JSON (Content-Type and body size already validated by middleware)
    raw_body = await request.body()
    try:
        parsed: object = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            error="invalid_json",
            message="Request body is not valid JSON",
            status_code=400,
            details={},
        ) from exc

    if not isinstance(parsed, dict):
        raise ServiceError(
            error="invalid_json",
            message="Request body must be a JSON object",
            status_code=400,
            details={},
        )

    data: dict[str, object] = parsed

    # --- JWS Token Extraction ---
    token = _extract_jws_token(data)

    # --- Local JWS verification via platform agent ---
    state = get_app_state()
    if state.platform_agent is None:
        raise ServiceError(
            error="service_not_ready",
            message="Platform agent not initialized",
            status_code=503,
            details={},
        )
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )

    try:
        payload_raw = state.platform_agent.validate_certificate(token)
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError(
            error="forbidden",
            message="JWS signature verification failed",
            status_code=403,
            details={},
        ) from exc
    except Exception as exc:
        raise ServiceError(
            error="identity_service_unavailable",
            message="Cannot reach Identity service",
            status_code=502,
            details={},
        ) from exc
    if not isinstance(payload_raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ServiceError(
            error="invalid_payload",
            message="JWS payload must be a JSON object",
            status_code=400,
            details={},
        )

    payload: dict[str, object] = payload_raw
    signer_agent_id: str = _extract_signer_agent_id(token)

    # --- Payload Validation ---
    action = payload.get("action")
    if action != "submit_feedback":
        raise ServiceError(
            error="invalid_payload",
            message='JWS payload action must be "submit_feedback"',
            status_code=400,
            details={},
        )

    from_agent_id_in_payload = payload.get("from_agent_id")
    if not from_agent_id_in_payload or not isinstance(from_agent_id_in_payload, str):
        raise ServiceError(
            error="invalid_payload",
            message="JWS payload must contain from_agent_id",
            status_code=400,
            details={},
        )

    # --- Authorization: Signer Matching ---
    if signer_agent_id != from_agent_id_in_payload:
        raise ServiceError(
            error="forbidden",
            message="Signer does not match from_agent_id in payload",
            status_code=403,
            details={},
        )

    # --- Extract feedback fields from JWS payload ---
    feedback_body: dict[str, object] = {k: v for k, v in payload.items() if k != "action"}

    result = submit_feedback(
        store=state.feedback_store,
        body=feedback_body,
        max_comment_length=settings.feedback.max_comment_length,
    )

    if isinstance(result, ValidationError):
        raise ServiceError(
            error=result.error,
            message=result.message,
            status_code=result.status_code,
            details=result.details,
        )

    return JSONResponse(
        status_code=201,
        content=_record_to_dict(result),
    )


@router.get("/feedback/task/{task_id}")
async def get_task_feedback(task_id: str) -> JSONResponse:
    """Get all visible feedback for a task."""
    settings = get_settings()
    state = get_app_state()
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )

    records = get_feedback_for_task(
        store=state.feedback_store,
        task_id=task_id,
        reveal_timeout_seconds=settings.feedback.reveal_timeout_seconds,
    )
    return JSONResponse(
        status_code=200,
        content={
            "task_id": task_id,
            "feedback": [_record_to_dict(r) for r in records],
        },
    )


@router.get("/feedback/agent/{agent_id}")
async def get_agent_feedback(agent_id: str) -> JSONResponse:
    """Get all visible feedback about an agent."""
    settings = get_settings()
    state = get_app_state()
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )

    records = get_feedback_for_agent(
        store=state.feedback_store,
        agent_id=agent_id,
        reveal_timeout_seconds=settings.feedback.reveal_timeout_seconds,
    )
    return JSONResponse(
        status_code=200,
        content={
            "agent_id": agent_id,
            "feedback": [_record_to_dict(r) for r in records],
        },
    )


@router.get("/feedback/{feedback_id}")
async def get_feedback(feedback_id: str) -> JSONResponse:
    """Look up a single feedback record."""
    settings = get_settings()
    state = get_app_state()
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )

    record = get_feedback_by_id(
        store=state.feedback_store,
        feedback_id=feedback_id,
        reveal_timeout_seconds=settings.feedback.reveal_timeout_seconds,
    )
    if record is None:
        raise ServiceError(
            error="feedback_not_found",
            message="Feedback not found",
            status_code=404,
            details={},
        )
    return JSONResponse(
        status_code=200,
        content=_record_to_dict(record),
    )
