"""Metrics route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state
from ui_service.schemas import (
    AgentMetrics,
    EconomyPhaseMetrics,
    EscrowMetrics,
    GDPDataPoint,
    GDPHistoryResponse,
    GDPMetrics,
    LaborMarketMetrics,
    MetricsResponse,
    RewardDistribution,
    SparklineMetrics,
    SparklineResponse,
    SpecQualityMetrics,
    TaskMetrics,
)
from ui_service.services import metrics as metrics_service

router = APIRouter()

VALID_WINDOWS = {"1h", "24h", "7d"}
VALID_RESOLUTIONS = {"1m", "5m", "1h"}
VALID_SPARKLINE_WINDOWS = {"24h"}


@router.get("/metrics")
async def get_metrics() -> JSONResponse:
    """Return aggregated economy metrics."""
    state = get_app_state()
    db = state.db
    if db is None:
        raise ServiceError(
            error="database_unavailable",
            message="Database not available yet",
            status_code=503,
            details=None,
        )

    agents_data = await metrics_service.compute_agents(db)
    active_agents = agents_data["active"]

    gdp_data = await metrics_service.compute_gdp(db, active_agents)
    tasks_data = await metrics_service.compute_tasks(db)
    escrow_data = await metrics_service.compute_escrow(db)
    spec_quality_data = await metrics_service.compute_spec_quality(db)
    labor_market_data = await metrics_service.compute_labor_market(db, active_agents)
    economy_phase_data = await metrics_service.compute_economy_phase(
        db,
        tasks_data["total_created"],
    )

    reward_dist = RewardDistribution(
        **labor_market_data["reward_distribution"],
    )

    response = MetricsResponse(
        gdp=GDPMetrics(**gdp_data),
        agents=AgentMetrics(**agents_data),
        tasks=TaskMetrics(**tasks_data),
        escrow=EscrowMetrics(**escrow_data),
        spec_quality=SpecQualityMetrics(**spec_quality_data),
        labor_market=LaborMarketMetrics(
            avg_bids_per_task=labor_market_data["avg_bids_per_task"],
            avg_reward=labor_market_data["avg_reward"],
            task_posting_rate=labor_market_data["task_posting_rate"],
            acceptance_latency_minutes=labor_market_data["acceptance_latency_minutes"],
            unemployment_rate=labor_market_data["unemployment_rate"],
            reward_distribution=reward_dist,
            delta_avg_bids=labor_market_data["delta_avg_bids"],
            delta_avg_reward=labor_market_data["delta_avg_reward"],
        ),
        economy_phase=EconomyPhaseMetrics(**economy_phase_data),
        computed_at=metrics_service.now_iso(),
    )

    return JSONResponse(content=response.model_dump(by_alias=True))


@router.get("/metrics/gdp/history")  # nosemgrep
async def get_gdp_history(
    window: str = Query("1h"),
    resolution: str = Query("1m"),
) -> JSONResponse:
    """Return GDP time series data."""
    if window not in VALID_WINDOWS:
        raise ServiceError(
            error="invalid_parameter",
            message=f"Invalid window: {window}. Must be one of: {', '.join(sorted(VALID_WINDOWS))}",
            status_code=400,
            details={"parameter": "window", "value": window},
        )

    if resolution not in VALID_RESOLUTIONS:
        raise ServiceError(
            error="invalid_parameter",
            message=(
                f"Invalid resolution: {resolution}. "
                f"Must be one of: {', '.join(sorted(VALID_RESOLUTIONS))}"
            ),
            status_code=400,
            details={"parameter": "resolution", "value": resolution},
        )

    state = get_app_state()
    db = state.db
    if db is None:
        raise ServiceError(
            error="database_unavailable",
            message="Database not available yet",
            status_code=503,
            details=None,
        )

    data_points_raw = await metrics_service.compute_gdp_history(db, window, resolution)

    data_points = [GDPDataPoint(**dp) for dp in data_points_raw]

    response = GDPHistoryResponse(
        window=window,
        resolution=resolution,
        data_points=data_points,
    )

    return JSONResponse(content=response.model_dump(by_alias=True))


@router.get("/metrics/sparklines")  # nosemgrep
async def get_sparklines(
    window: str = Query("24h"),
) -> JSONResponse:
    """Return all metric sparkline time series for the exchange board."""
    if window not in VALID_SPARKLINE_WINDOWS:
        raise ServiceError(
            error="invalid_parameter",
            message=(
                f"Invalid window: {window}. "
                f"Must be one of: {', '.join(sorted(VALID_SPARKLINE_WINDOWS))}"
            ),
            status_code=400,
            details={"parameter": "window", "value": window},
        )

    state = get_app_state()
    db = state.db
    if db is None:
        raise ServiceError(
            error="database_unavailable",
            message="Database not available yet",
            status_code=503,
            details=None,
        )

    raw = await metrics_service.compute_sparkline_history(db, window)

    response = SparklineResponse(
        window=raw["window"],
        buckets=raw["buckets"],
        metrics=SparklineMetrics(**raw["metrics"]),
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
