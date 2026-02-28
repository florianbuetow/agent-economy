"""Metrics route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from observatory_service.core.state import get_app_state
from observatory_service.schemas import (
    AgentMetrics,
    EconomyPhaseMetrics,
    EscrowMetrics,
    GDPDataPoint,
    GDPHistoryResponse,
    GDPMetrics,
    LaborMarketMetrics,
    MetricsResponse,
    RewardDistribution,
    SpecQualityMetrics,
    TaskMetrics,
)
from observatory_service.services import metrics as metrics_service

router = APIRouter()

VALID_WINDOWS = {"1h", "24h", "7d"}
VALID_RESOLUTIONS = {"1m", "5m", "1h"}


@router.get("/metrics")
async def get_metrics() -> JSONResponse:
    """Return aggregated economy metrics."""
    state = get_app_state()
    db = state.db
    assert db is not None

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
            error="INVALID_PARAMETER",
            message=f"Invalid window: {window}. Must be one of: {', '.join(sorted(VALID_WINDOWS))}",
            status_code=400,
            details={"parameter": "window", "value": window},
        )

    if resolution not in VALID_RESOLUTIONS:
        raise ServiceError(
            error="INVALID_PARAMETER",
            message=(
                f"Invalid resolution: {resolution}. "
                f"Must be one of: {', '.join(sorted(VALID_RESOLUTIONS))}"
            ),
            status_code=400,
            details={"parameter": "resolution", "value": resolution},
        )

    state = get_app_state()
    db = state.db
    assert db is not None

    data_points_raw = await metrics_service.compute_gdp_history(db, window, resolution)

    data_points = [GDPDataPoint(**dp) for dp in data_points_raw]

    response = GDPHistoryResponse(
        window=window,
        resolution=resolution,
        data_points=data_points,
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
