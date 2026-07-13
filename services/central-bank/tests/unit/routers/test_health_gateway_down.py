"""GAP-D7: GET /health must degrade cleanly (503), not crash (500), when the
DB Gateway backing the ledger is unreachable.

Reproduces the real failure path: LedgerDbClient.count_accounts()/total_escrowed()
wrap a GatewayClient connection failure in a bare RuntimeError("Gateway error: ...")
(see central_bank_service/services/ledger_db_client.py:_as_gateway_runtime_error).
Before the fix, that RuntimeError went uncaught in the health router and fell
through to the generic unhandled_exception_handler, producing a 500.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state


class _DownLedger:
    """Fake ledger whose read methods fail exactly like a downed DB Gateway."""

    count_accounts = AsyncMock(
        side_effect=RuntimeError("Gateway error: 502 db_gateway_unavailable")
    )
    total_escrowed = AsyncMock(
        side_effect=RuntimeError("Gateway error: 502 db_gateway_unavailable")
    )
    close = AsyncMock()


@pytest.mark.unit
async def test_health_degrades_when_gateway_down(client):
    """GET /health returns 503 service_not_ready (not 500) when the ledger's
    backing DB Gateway is unreachable."""
    state = get_app_state()
    state.ledger = _DownLedger()

    response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "service_not_ready"
