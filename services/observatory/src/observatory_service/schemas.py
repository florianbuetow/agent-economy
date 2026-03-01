"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Health / Error
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    """Response model for GET /health."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    uptime_seconds: float
    started_at: str
    latest_event_id: int
    database_readable: bool


class ErrorResponse(BaseModel):
    """Standard error response model."""

    model_config = ConfigDict(extra="forbid")
    error: str
    message: str
    details: dict[str, object]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
class RewardDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    field_0_to_10: int = Field(alias="0_to_10")
    field_11_to_50: int = Field(alias="11_to_50")
    field_51_to_100: int = Field(alias="51_to_100")
    over_100: int


class GDPMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    last_24h: int
    last_7d: int
    per_agent: float
    rate_per_hour: float


class AgentMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_registered: int
    active: int
    with_completed_tasks: int


class TaskMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_created: int
    completed_all_time: int
    completed_24h: int
    open: int
    in_execution: int
    disputed: int
    completion_rate: float


class EscrowMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_locked: int


class SpecQualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_score: float
    extremely_satisfied_pct: float
    satisfied_pct: float
    dissatisfied_pct: float
    trend_direction: str
    trend_delta: float


class LaborMarketMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_bids_per_task: float
    avg_reward: float
    task_posting_rate: float
    acceptance_latency_minutes: float
    unemployment_rate: float
    reward_distribution: RewardDistribution


class EconomyPhaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: str
    task_creation_trend: str
    dispute_rate: float


class MetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    gdp: GDPMetrics
    agents: AgentMetrics
    tasks: TaskMetrics
    escrow: EscrowMetrics
    spec_quality: SpecQualityMetrics
    labor_market: LaborMarketMetrics
    economy_phase: EconomyPhaseMetrics
    computed_at: str


# ---------------------------------------------------------------------------
# GDP History
# ---------------------------------------------------------------------------
class GDPDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str
    gdp: int


class GDPHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window: str
    resolution: str
    data_points: list[GDPDataPoint]


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
class SpecQualityStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extremely_satisfied: int
    satisfied: int
    dissatisfied: int


class DeliveryQualityStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extremely_satisfied: int
    satisfied: int
    dissatisfied: int


class AgentStats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks_posted: int
    tasks_completed_as_worker: int
    total_earned: int
    total_spent: int
    spec_quality: SpecQualityStats
    delivery_quality: DeliveryQualityStats


class AgentListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    name: str
    registered_at: str
    stats: AgentStats


class AgentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agents: list[AgentListItem]
    total_count: int
    limit: int
    offset: int


class AgentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    name: str


class RecentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str
    role: str
    status: str
    reward: int
    completed_at: str | None


class FeedbackItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_id: str
    task_id: str
    from_agent_name: str
    category: str
    rating: str
    comment: str | None
    submitted_at: str


class AgentProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    name: str
    registered_at: str
    balance: int
    stats: AgentStats
    recent_tasks: list[RecentTask]
    recent_feedback: list[FeedbackItem]


class AgentFeedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int
    event_source: str
    event_type: str
    timestamp: str
    task_id: str | None
    agent_id: str | None
    summary: str
    payload: dict[str, object]
    badge: str
    role: str | None
    task_title: str | None
    task_reward: int | None
    poster_id: str | None
    worker_id: str | None
    poster_name: str | None
    worker_name: str | None


class AgentFeedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[AgentFeedEvent]
    has_more: bool


class EarningsDataPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: str
    cumulative: int


class AgentEarningsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_points: list[EarningsDataPoint]
    total_earned: int
    last_7d_earned: int
    avg_per_task: int
    tasks_approved: int


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
class BidderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    name: str
    delivery_quality: DeliveryQualityStats


class BidItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bid_id: str
    bidder: BidderInfo
    proposal: str
    submitted_at: str
    accepted: bool


class AssetItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    filename: str
    content_type: str
    size_bytes: int
    uploaded_at: str


class FeedbackDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_id: str
    from_agent_name: str
    to_agent_name: str
    category: str
    rating: str
    comment: str | None
    visible: bool


class DisputeRebuttal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    submitted_at: str


class DisputeRuling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruling_id: str
    worker_pct: int
    summary: str
    ruled_at: str


class DisputeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    reason: str
    filed_at: str
    rebuttal: DisputeRebuttal | None
    ruling: DisputeRuling | None


class TaskDeadlines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bidding_deadline: str
    execution_deadline: str | None
    review_deadline: str | None


class TaskTimestamps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    created_at: str
    accepted_at: str | None
    submitted_at: str | None
    approved_at: str | None


class TaskDrilldownResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    poster: AgentRef
    worker: AgentRef | None
    title: str
    spec: str
    reward: int
    status: str
    deadlines: TaskDeadlines
    timestamps: TaskTimestamps
    bids: list[BidItem]
    assets: list[AssetItem]
    feedback: list[FeedbackDetail]
    dispute: DisputeInfo | None


class CompetitiveTaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str
    reward: int
    status: str
    bid_count: int
    poster: AgentRef
    created_at: str
    bidding_deadline: str


class CompetitiveTasksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[CompetitiveTaskItem]


class UncontestedTaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str
    reward: int
    poster: AgentRef
    created_at: str
    bidding_deadline: str
    minutes_without_bids: float


class UncontestedTasksResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[UncontestedTaskItem]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
class EventItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: int
    event_source: str
    event_type: str
    timestamp: str
    task_id: str | None
    agent_id: str | None
    summary: str
    payload: dict[str, object]


class EventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[EventItem]
    has_more: bool
    oldest_event_id: int | None
    newest_event_id: int | None


# ---------------------------------------------------------------------------
# Quarterly
# ---------------------------------------------------------------------------
class QuarterlyPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: str
    end: str


class QuarterlyGDP(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    previous_quarter: int
    delta_pct: float
    per_agent: float


class QuarterlyTasks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    posted: int
    completed: int
    disputed: int
    completion_rate: float


class QuarterlyLaborMarket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_bids_per_task: float
    avg_time_to_acceptance_minutes: float
    avg_reward: float


class QuarterlySpecQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_score: float
    previous_quarter_avg: float
    delta_pct: float


class QuarterlyAgents(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_registrations: int
    total_at_quarter_end: int


class NotableTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str
    title: str
    reward: int | None = None
    bid_count: int | None = None


class NotableAgent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    name: str
    earned: int | None = None
    spent: int | None = None


class QuarterlyNotable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    highest_value_task: NotableTask | None
    most_competitive_task: NotableTask | None
    top_workers: list[NotableAgent]
    top_posters: list[NotableAgent]


class QuarterlyReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quarter: str
    period: QuarterlyPeriod
    gdp: QuarterlyGDP
    tasks: QuarterlyTasks
    labor_market: QuarterlyLaborMarket
    spec_quality: QuarterlySpecQuality
    agents: QuarterlyAgents
    notable: QuarterlyNotable
