"""
Pydantic request/response models for the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Response model for GET /health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    uptime_seconds: float
    started_at: str
    total_feedback: int


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, Any]


class FeedbackResponse(BaseModel):
    """Response model for a single feedback record."""

    model_config = ConfigDict(extra="forbid")
    feedback_id: str
    task_id: str
    from_agent_id: str
    to_agent_id: str
    category: str
    rating: str
    comment: str | None
    submitted_at: str
    visible: bool


class TaskFeedbackResponse(BaseModel):
    """Response model for GET /feedback/task/{task_id}."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    feedback: list[FeedbackResponse]


class AgentFeedbackResponse(BaseModel):
    """Response model for GET /feedback/agent/{agent_id}."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str
    feedback: list[FeedbackResponse]
