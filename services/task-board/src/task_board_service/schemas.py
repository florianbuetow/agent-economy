"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Response model for GET /health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    uptime_seconds: float
    started_at: str
    total_tasks: int
    tasks_by_status: dict[str, int]


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]


class TaskResponse(BaseModel):
    """Full task detail response model."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    poster_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    deadline_seconds: int
    review_deadline_seconds: int
    status: str
    escrow_id: str
    bid_count: int
    worker_id: str | None
    accepted_bid_id: str | None
    created_at: str
    accepted_at: str | None
    submitted_at: str | None
    approved_at: str | None
    cancelled_at: str | None
    disputed_at: str | None
    dispute_reason: str | None
    ruling_id: str | None
    ruled_at: str | None
    worker_pct: int | None
    ruling_summary: str | None
    expired_at: str | None
    escrow_pending: bool
    bidding_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskSummary(BaseModel):
    """Summary task model for list views."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    poster_id: str
    title: str
    reward: int
    status: str
    bid_count: int
    worker_id: str | None
    created_at: str
    bidding_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskListResponse(BaseModel):
    """Response model for GET /tasks."""

    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskSummary]


class BidResponse(BaseModel):
    """Response model for a single bid."""

    model_config = ConfigDict(extra="forbid")
    bid_id: str
    task_id: str
    bidder_id: str
    proposal: str
    submitted_at: str


class BidListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/bids."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    bids: list[BidResponse]


class AssetResponse(BaseModel):
    """Response model for a single asset."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str
    task_id: str
    uploader_id: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: str


class AssetListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/assets."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    assets: list[AssetResponse]
