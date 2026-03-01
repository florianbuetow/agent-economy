"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from central_bank_service.core.state import get_app_state
from central_bank_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    total_accounts = 0
    total_escrowed = 0
    if state.ledger is not None:
        total_accounts = state.ledger.count_accounts()
        total_escrowed = state.ledger.total_escrowed()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_accounts=total_accounts,
        total_escrowed=total_escrowed,
    )
