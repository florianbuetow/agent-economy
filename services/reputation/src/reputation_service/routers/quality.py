"""Quality score endpoint: ``GET /agents/{agent_id}/quality``."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_auth.jws import JwsAdmissionError, require_context
from starlette.concurrency import run_in_threadpool

from reputation_service.core.exceptions import ServiceError
from reputation_service.core.state import get_app_state
from reputation_service.services.quality import compute_quality, provenance_filter

if TYPE_CHECKING:
    from reputation_service.core.state import AppState
    from reputation_service.services.protocol import TaskLookup
    from reputation_service.services.task_board_client import TaskParties

router = APIRouter()

QUALITY_ACTION = "get_quality"


def _not_ready(component: str) -> ServiceError:
    return ServiceError("service_not_ready", f"{component} not initialized", 503, {})


async def _signed_viewer(state: AppState, request: Request, agent_id: str) -> str | None:
    """Return the authenticated viewer id, or None for a public unsigned read.

    A present but invalid ``Authorization`` header is rejected rather than being
    downgraded to a public read.
    """
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ServiceError("invalid_jws", "Authorization must be 'Bearer <JWS>'", 400, {})
    if state.identity_client is None:
        raise _not_ready("Identity client")
    try:
        verified = await state.identity_client.verify_jws(token)
    except InvalidSignature as exc:
        raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc
    except JwsAdmissionError as exc:
        raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
    try:
        require_context(
            verified,
            audience="reputation",
            resource=f"GET /agents/{agent_id}/quality",
            allowed_action=QUALITY_ACTION,
        )
    except JwsAdmissionError as exc:
        raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
    return verified.header.kid


async def _is_operator(state: AppState, viewer_id: str) -> bool:
    if state.agent_directory is None:
        raise _not_ready("Agent directory")
    agent = await state.agent_directory.get_agent(viewer_id)
    return agent is not None and agent.get("name") == state.quality_operator_name


async def _lookup(board: TaskLookup, task_id: str) -> TaskParties | None:
    """Task facts, or None for a task Task Board does not know (never eligible)."""
    try:
        return await board.get_task(task_id)
    except ServiceError as exc:
        if exc.status_code == 404:
            return None
        raise


@router.get("/agents/{agent_id}/quality")
async def get_agent_quality(agent_id: str, request: Request) -> JSONResponse:
    """Public quality scores and counts; provenance for authorized signed readers."""
    state = get_app_state()
    if state.feedback_store is None:
        raise _not_ready("Feedback store")
    if state.task_board_client is None:
        raise _not_ready("Task Board client")
    board = state.task_board_client

    viewer_id = await _signed_viewer(state, request, agent_id)
    records = await run_in_threadpool(state.feedback_store.get_by_agent, agent_id)
    task_ids = sorted({r.task_id for r in records if r.visible})
    facts = await asyncio.gather(*(_lookup(board, task_id) for task_id in task_ids))
    tasks = dict(zip(task_ids, facts, strict=True))

    viewer_is_operator = (
        viewer_id is not None and viewer_id != agent_id and await _is_operator(state, viewer_id)
    )
    can_see_task = provenance_filter(
        viewer_id,
        subject_id=agent_id,
        viewer_is_operator=viewer_is_operator,
        tasks=tasks,
    )
    return JSONResponse(
        status_code=200,
        content=compute_quality(agent_id, records, tasks, can_see_task),
    )
