"""Pydantic request/response models for the API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from task_board_service.task_contract_projection import TaskContractProjection
from task_board_service.task_operation_contract import OperationEnvelope


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


class TaskPublicationBlockedDetails(BaseModel):
    """Safe persisted reason for an integrity-blocked publication."""

    model_config = ConfigDict(extra="forbid", strict=True)
    reason: Literal[
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    ]


class TaskPublicationBlockedError(BaseModel):
    """Non-recursive error frozen inside a blocked publication envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)
    error: Literal["publication_integrity_blocked"]
    message: Literal["Publication evidence requires reconciliation"]
    details: TaskPublicationBlockedDetails


class TaskPublicationResult(BaseModel):
    """Immutable task-creation result frozen at atomic publication."""

    model_config = ConfigDict(extra="forbid", strict=True)
    task_id: str
    poster_id: str
    title: str
    spec: str
    reward: int
    bidding_deadline_seconds: int
    acceptance_window_seconds: int
    execution_deadline_seconds: int
    review_deadline_seconds: int
    status: Literal["open"]
    escrow_id: str
    created_at: str
    bidding_deadline: str
    acceptance_deadline: str
    bidding_cutoff_us: int
    acceptance_cutoff_us: int
    lifecycle_version: Literal[0]
    timing_contract_version: Literal[2]
    event_id: int


class TaskPublicationEnvelope(BaseModel):
    """Exact public acknowledgment for one native task publication."""

    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]
    operation_id: str = Field(
        pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    task_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_state: Literal["awaiting_lock", "published", "blocked_integrity"]
    lock_operation_id: str = Field(
        pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    escrow_id: str | None
    admitted_at: str
    published_at: str | None
    result: TaskPublicationResult | None
    error: TaskPublicationBlockedError | None


class TaskResponse(TaskContractProjection):
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
    dispute_id: str | None
    rebuttal_deadline: str | None
    rebuttal_submitted_at: str | None
    ruling_id: str | None
    ruled_at: str | None
    worker_pct: int | None
    ruling_summary: str | None
    expired_at: str | None
    escrow_pending: bool
    bidding_deadline: str
    acceptance_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskMutationResponse(TaskResponse):
    """Current task view plus the separately preserved immutable command outcome."""

    operation: OperationEnvelope


class TaskSummary(TaskContractProjection):
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
    acceptance_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskListResponse(BaseModel):
    """Response model for GET /tasks."""

    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskSummary]


class BidResponse(BaseModel):
    """Response model for POST /tasks/{task_id}/bids.

    ``amount``, not ``proposal`` — the real bidding model is undercutting on a
    positive-integer amount (see task_manager.submit_bid), not a free-text
    proposal. The published API spec still describes the old proposal-only
    model; that doc correction is tracked separately (T-024, WP-12).
    """

    model_config = ConfigDict(extra="forbid")
    bid_id: str
    task_id: str
    bidder_id: str
    amount: int
    submitted_at: str


class BidSummary(BaseModel):
    """Bid list-item model for GET /tasks/{task_id}/bids — no task_id per item."""

    model_config = ConfigDict(extra="forbid")
    bid_id: str
    bidder_id: str
    amount: int
    submitted_at: str


class BidListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/bids."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    bids: list[BidSummary]


class AssetResponse(BaseModel):
    """Response model for POST /tasks/{task_id}/assets."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str
    task_id: str
    uploader_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    uploaded_at: str


class AssetSummary(BaseModel):
    """Asset list-item model for GET /tasks/{task_id}/assets — no task_id per item."""

    model_config = ConfigDict(extra="forbid")
    asset_id: str
    uploader_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    uploaded_at: str


class AssetListResponse(BaseModel):
    """Response model for GET /tasks/{task_id}/assets."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    assets: list[AssetSummary]
