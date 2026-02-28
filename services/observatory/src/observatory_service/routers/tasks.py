"""Tasks route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from observatory_service.core.state import get_app_state
from observatory_service.schemas import (
    AgentRef,
    AssetItem,
    BidderInfo,
    BidItem,
    CompetitiveTaskItem,
    CompetitiveTasksResponse,
    DeliveryQualityStats,
    DisputeInfo,
    DisputeRebuttal,
    DisputeRuling,
    FeedbackDetail,
    TaskDeadlines,
    TaskDrilldownResponse,
    TaskTimestamps,
    UncontestedTaskItem,
    UncontestedTasksResponse,
)
from observatory_service.services import tasks as tasks_service

router = APIRouter()


@router.get("/tasks/-/competitive")
async def get_competitive_tasks(
    limit: int = Query(5, ge=1, le=20),
    status: str = Query("open"),
) -> JSONResponse:
    """Return tasks sorted by bid count descending."""
    state = get_app_state()
    db = state.db

    data = await tasks_service.get_competitive_tasks(db, limit=limit, status=status)

    tasks = [
        CompetitiveTaskItem(
            task_id=t["task_id"],
            title=t["title"],
            reward=t["reward"],
            status=t["status"],
            bid_count=t["bid_count"],
            poster=AgentRef(**t["poster"]),
            created_at=t["created_at"],
            bidding_deadline=t["bidding_deadline"],
        )
        for t in data
    ]

    response = CompetitiveTasksResponse(tasks=tasks)
    return JSONResponse(content=response.model_dump(by_alias=True))


@router.get("/tasks/-/uncontested")
async def get_uncontested_tasks(
    min_age_minutes: int = Query(10, ge=0),
    limit: int = Query(10, ge=1, le=50),
) -> JSONResponse:
    """Return open tasks with zero bids older than min_age_minutes."""
    state = get_app_state()
    db = state.db

    data = await tasks_service.get_uncontested_tasks(
        db, min_age_minutes=min_age_minutes, limit=limit
    )

    tasks = [
        UncontestedTaskItem(
            task_id=t["task_id"],
            title=t["title"],
            reward=t["reward"],
            poster=AgentRef(**t["poster"]),
            created_at=t["created_at"],
            bidding_deadline=t["bidding_deadline"],
            minutes_without_bids=t["minutes_without_bids"],
        )
        for t in data
    ]

    response = UncontestedTasksResponse(tasks=tasks)
    return JSONResponse(content=response.model_dump(by_alias=True))


@router.get("/tasks/{task_id}")
async def get_task_drilldown(task_id: str) -> JSONResponse:
    """Return full task drilldown with bids, assets, feedback, and dispute."""
    state = get_app_state()
    db = state.db

    data = await tasks_service.get_task_drilldown(db, task_id)

    if data is None:
        raise ServiceError(
            error="TASK_NOT_FOUND",
            message=f"Task '{task_id}' not found",
            status_code=404,
            details={"task_id": task_id},
        )

    bids = [
        BidItem(
            bid_id=b["bid_id"],
            bidder=BidderInfo(
                agent_id=b["bidder"]["agent_id"],
                name=b["bidder"]["name"],
                delivery_quality=DeliveryQualityStats(**b["bidder"]["delivery_quality"]),
            ),
            proposal=b["proposal"],
            submitted_at=b["submitted_at"],
            accepted=b["accepted"],
        )
        for b in data["bids"]
    ]

    assets = [
        AssetItem(
            asset_id=a["asset_id"],
            filename=a["filename"],
            content_type=a["content_type"],
            size_bytes=a["size_bytes"],
            uploaded_at=a["uploaded_at"],
        )
        for a in data["assets"]
    ]

    feedback = [
        FeedbackDetail(
            feedback_id=fb["feedback_id"],
            from_agent_name=fb["from_agent_name"],
            to_agent_name=fb["to_agent_name"],
            category=fb["category"],
            rating=fb["rating"],
            comment=fb["comment"],
            visible=fb["visible"],
        )
        for fb in data["feedback"]
    ]

    dispute = None
    if data["dispute"] is not None:
        d = data["dispute"]
        rebuttal = None
        if d["rebuttal"] is not None:
            rebuttal = DisputeRebuttal(
                content=d["rebuttal"]["content"],
                submitted_at=d["rebuttal"]["submitted_at"],
            )
        ruling = None
        if d["ruling"] is not None:
            ruling = DisputeRuling(
                ruling_id=d["ruling"]["ruling_id"],
                worker_pct=d["ruling"]["worker_pct"],
                summary=d["ruling"]["summary"],
                ruled_at=d["ruling"]["ruled_at"],
            )
        dispute = DisputeInfo(
            claim_id=d["claim_id"],
            reason=d["reason"],
            filed_at=d["filed_at"],
            rebuttal=rebuttal,
            ruling=ruling,
        )

    worker = None
    if data["worker"] is not None:
        worker = AgentRef(**data["worker"])

    response = TaskDrilldownResponse(
        task_id=data["task_id"],
        poster=AgentRef(**data["poster"]),
        worker=worker,
        title=data["title"],
        spec=data["spec"],
        reward=data["reward"],
        status=data["status"],
        deadlines=TaskDeadlines(**data["deadlines"]),
        timestamps=TaskTimestamps(**data["timestamps"]),
        bids=bids,
        assets=assets,
        feedback=feedback,
        dispute=dispute,
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
