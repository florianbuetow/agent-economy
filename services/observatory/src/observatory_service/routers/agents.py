"""Agents route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from observatory_service.core.state import get_app_state
from observatory_service.schemas import (
    AgentListItem,
    AgentListResponse,
    AgentProfileResponse,
    AgentStats,
    DeliveryQualityStats,
    FeedbackItem,
    RecentTask,
    SpecQualityStats,
)
from observatory_service.services import agents as agents_service

router = APIRouter()

VALID_SORT_FIELDS = {
    "total_earned",
    "total_spent",
    "tasks_completed",
    "tasks_posted",
    "spec_quality",
    "delivery_quality",
}


@router.get("/agents")  # nosemgrep
async def list_agents(
    sort_by: str = Query("total_earned"),
    order: str = Query("desc"),
    limit: int = Query(20),
    offset: int = Query(0),
) -> JSONResponse:
    """Return paginated list of agents with computed stats."""
    if sort_by not in VALID_SORT_FIELDS:
        valid = ", ".join(sorted(VALID_SORT_FIELDS))
        raise ServiceError(
            error="INVALID_PARAMETER",
            message=f"Invalid sort_by: {sort_by}. Must be one of: {valid}",
            status_code=400,
            details={"parameter": "sort_by", "value": sort_by},
        )

    state = get_app_state()
    db = state.db
    assert db is not None

    data = await agents_service.list_agents(db, sort_by, order, limit, offset)

    agents = [
        AgentListItem(
            agent_id=a["agent_id"],
            name=a["name"],
            registered_at=a["registered_at"],
            stats=AgentStats(
                tasks_posted=a["stats"]["tasks_posted"],
                tasks_completed_as_worker=a["stats"]["tasks_completed_as_worker"],
                total_earned=a["stats"]["total_earned"],
                total_spent=a["stats"]["total_spent"],
                spec_quality=SpecQualityStats(**a["stats"]["spec_quality"]),
                delivery_quality=DeliveryQualityStats(**a["stats"]["delivery_quality"]),
            ),
        )
        for a in data["agents"]
    ]

    response = AgentListResponse(
        agents=agents,
        total_count=data["total_count"],
        limit=data["limit"],
        offset=data["offset"],
    )

    return JSONResponse(content=response.model_dump(by_alias=True))


@router.get("/agents/{agent_id}")
async def get_agent_profile(agent_id: str) -> JSONResponse:
    """Return a single agent's full profile."""
    state = get_app_state()
    db = state.db
    assert db is not None

    data = await agents_service.get_agent_profile(db, agent_id)

    if data is None:
        raise ServiceError(
            error="AGENT_NOT_FOUND",
            message=f"Agent '{agent_id}' not found",
            status_code=404,
            details={"agent_id": agent_id},
        )

    recent_tasks = [
        RecentTask(
            task_id=t["task_id"],
            title=t["title"],
            role=t["role"],
            status=t["status"],
            reward=t["reward"],
            completed_at=t["completed_at"],
        )
        for t in data["recent_tasks"]
    ]

    recent_feedback = [
        FeedbackItem(
            feedback_id=fb["feedback_id"],
            task_id=fb["task_id"],
            from_agent_name=fb["from_agent_name"],
            category=fb["category"],
            rating=fb["rating"],
            comment=fb["comment"],
            submitted_at=fb["submitted_at"],
        )
        for fb in data["recent_feedback"]
    ]

    response = AgentProfileResponse(
        agent_id=data["agent_id"],
        name=data["name"],
        registered_at=data["registered_at"],
        balance=data["balance"],
        stats=AgentStats(
            tasks_posted=data["stats"]["tasks_posted"],
            tasks_completed_as_worker=data["stats"]["tasks_completed_as_worker"],
            total_earned=data["stats"]["total_earned"],
            total_spent=data["stats"]["total_spent"],
            spec_quality=SpecQualityStats(**data["stats"]["spec_quality"]),
            delivery_quality=DeliveryQualityStats(**data["stats"]["delivery_quality"]),
        ),
        recent_tasks=recent_tasks,
        recent_feedback=recent_feedback,
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
