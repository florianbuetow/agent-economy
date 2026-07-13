"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state
from central_bank_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics.

    GAP-D7: the ledger's read methods hit the DB Gateway over HTTP; a downed
    gateway must degrade this endpoint to a clean 503, not crash it with an
    unhandled 500 (LedgerDbClient wraps a gateway connection failure in a bare
    RuntimeError — see ledger_db_client.py:_as_gateway_runtime_error).
    """
    state = get_app_state()
    total_accounts = 0
    total_escrowed = 0
    if state.ledger is not None:
        try:
            total_accounts = await state.ledger.count_accounts()
            total_escrowed = await state.ledger.total_escrowed()
        except RuntimeError as exc:
            raise ServiceError(
                "service_not_ready",
                "Ledger's DB Gateway is unavailable",
                503,
                {},
            ) from exc
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_accounts=total_accounts,
        total_escrowed=total_escrowed,
    )
