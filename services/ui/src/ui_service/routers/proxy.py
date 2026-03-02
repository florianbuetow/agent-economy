"""Proxy route handlers — task lifecycle via UserAgent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ui_service.schemas import (
    CreateTaskRequest,
    FileDisputeRequest,
    ProxyIdentityResponse,
)
from ui_service.services import proxy as proxy_service

router = APIRouter()


@router.get("/proxy/identity")
async def get_identity() -> ProxyIdentityResponse:
    """Return the UserAgent's agent_id."""
    agent_id = await proxy_service.get_identity()
    return ProxyIdentityResponse(agent_id=agent_id)


@router.post("/proxy/tasks")
async def create_task(body: CreateTaskRequest) -> dict[str, Any]:
    """Create a new task via UserAgent."""
    return await proxy_service.create_task(
        title=body.title,
        spec=body.spec,
        reward=body.reward,
        bidding_deadline_seconds=body.bidding_deadline_seconds,
        execution_deadline_seconds=body.execution_deadline_seconds,
        review_deadline_seconds=body.review_deadline_seconds,
    )


@router.post("/proxy/tasks/{task_id}/bids/{bid_id}/accept")
async def accept_bid(task_id: str, bid_id: str) -> dict[str, Any]:
    """Accept a bid on a task."""
    return await proxy_service.accept_bid(task_id=task_id, bid_id=bid_id)


@router.post("/proxy/tasks/{task_id}/approve")
async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a submitted task."""
    return await proxy_service.approve_task(task_id=task_id)


@router.post("/proxy/tasks/{task_id}/dispute")
async def file_dispute(task_id: str, body: FileDisputeRequest) -> dict[str, Any]:
    """File a dispute on a task."""
    return await proxy_service.file_dispute(task_id=task_id, reason=body.reason)
