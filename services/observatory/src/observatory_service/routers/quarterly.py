"""Quarterly report route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from observatory_service.core.state import get_app_state
from observatory_service.schemas import (
    NotableAgent,
    NotableTask,
    QuarterlyAgents,
    QuarterlyGDP,
    QuarterlyLaborMarket,
    QuarterlyNotable,
    QuarterlyPeriod,
    QuarterlyReportResponse,
    QuarterlySpecQuality,
    QuarterlyTasks,
)
from observatory_service.services import quarterly as quarterly_service

router = APIRouter()


@router.get("/quarterly-report")
async def get_quarterly_report(
    quarter: str = Query(None),
) -> JSONResponse:
    """Return quarterly report for the specified or current quarter."""
    if quarter is None:
        quarter = quarterly_service.current_quarter_label()

    # Validate quarter format
    try:
        quarterly_service.validate_quarter(quarter)
    except ValueError:
        raise ServiceError(
            error="INVALID_QUARTER",
            message=f"Malformed quarter string: '{quarter}'. Must match YYYY-QN where N is 1-4.",
            status_code=400,
            details={"quarter": quarter},
        )

    state = get_app_state()
    db = state.db

    result = await quarterly_service.get_quarterly_report(db, quarter)

    if result is None:
        raise ServiceError(
            error="NO_DATA",
            message=f"No economy data exists for quarter {quarter}",
            status_code=404,
            details={"quarter": quarter},
        )

    # Build notable
    hvt = None
    if result["notable"]["highest_value_task"] is not None:
        hvt = NotableTask(**result["notable"]["highest_value_task"])

    mct = None
    if result["notable"]["most_competitive_task"] is not None:
        mct = NotableTask(**result["notable"]["most_competitive_task"])

    top_workers = [NotableAgent(**w) for w in result["notable"]["top_workers"]]
    top_posters = [NotableAgent(**p) for p in result["notable"]["top_posters"]]

    response = QuarterlyReportResponse(
        quarter=result["quarter"],
        period=QuarterlyPeriod(**result["period"]),
        gdp=QuarterlyGDP(**result["gdp"]),
        tasks=QuarterlyTasks(**result["tasks"]),
        labor_market=QuarterlyLaborMarket(**result["labor_market"]),
        spec_quality=QuarterlySpecQuality(**result["spec_quality"]),
        agents=QuarterlyAgents(**result["agents"]),
        notable=QuarterlyNotable(
            highest_value_task=hvt,
            most_competitive_task=mct,
            top_workers=top_workers,
            top_posters=top_posters,
        ),
    )

    return JSONResponse(content=response.model_dump(by_alias=True))
