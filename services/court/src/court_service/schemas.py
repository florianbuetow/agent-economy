"""Pydantic request/response models for the Court API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Response model for GET /health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    uptime_seconds: float
    started_at: str
    total_disputes: int
    active_disputes: int


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]


class VoteResponse(BaseModel):
    """Judge vote response model."""

    model_config = ConfigDict(extra="forbid")
    vote_id: str
    dispute_id: str
    judge_id: str
    worker_pct: int
    reasoning: str
    voted_at: str


class DisputeResponse(BaseModel):
    """Full dispute response model."""

    model_config = ConfigDict(extra="forbid")
    dispute_id: str
    task_id: str
    claimant_id: str
    respondent_id: str
    claim: str
    rebuttal: str | None
    status: str
    rebuttal_deadline: str
    worker_pct: int | None
    ruling_summary: str | None
    escrow_id: str
    filed_at: str
    rebutted_at: str | None
    ruled_at: str | None
    votes: list[VoteResponse]


class DisputeSummary(BaseModel):
    """List-view dispute summary model."""

    model_config = ConfigDict(extra="forbid")
    dispute_id: str
    task_id: str
    claimant_id: str
    respondent_id: str
    status: str
    worker_pct: int | None
    filed_at: str
    ruled_at: str | None


class DisputeListResponse(BaseModel):
    """List disputes response model."""

    model_config = ConfigDict(extra="forbid")
    disputes: list[DisputeSummary]
