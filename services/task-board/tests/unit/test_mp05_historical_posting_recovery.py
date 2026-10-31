"""Historical financial task references survive the native posting cutover unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.posting_recovery_contract import validate_legacy_posting_envelope
from task_board_service.services.posting_recovery import PostingRecovery
from tests.unit.test_mp05_posting_recovery_client import (
    OBSERVATION_ID,
    canonical_refund_hash,
    legacy_refund_envelope,
    pending_refund_envelope,
    recovery_client_graph,
    signer,
)

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

pytestmark = pytest.mark.unit
__all__ = ["signer"]


def _historical_pending(task_id: str) -> dict[str, object]:
    envelope = pending_refund_envelope()
    request = envelope["refund_request"]
    assert isinstance(request, dict)
    envelope["task_id"] = task_id
    request["source_reference"] = f"task:{task_id}:posting_refund:{envelope['escrow_id']}"
    envelope["refund_request_sha256"] = canonical_refund_hash(request)
    return envelope


@pytest.mark.parametrize("task_id", ["t-bank-only", "historique:économie"])
@pytest.mark.parametrize("state", ["refund_pending", "refunded"])
def test_legacy_response_preserves_non_native_task_identity(
    signer: GatewayRequestSigner, task_id: str, state: str
) -> None:
    envelope = (
        _historical_pending(task_id) if state == "refund_pending" else legacy_refund_envelope()
    )
    envelope["task_id"] = task_id
    status = 202 if state == "refund_pending" else 200
    graph = recovery_client_graph(signer, [httpx.Response(status, json=envelope)])
    try:
        assert graph.client.reconcile_legacy_posting_observation(OBSERVATION_ID) == envelope
    finally:
        graph.client.close()


@pytest.mark.parametrize("task_id", ["", None, 1, "\ud800"])
def test_legacy_task_identity_still_requires_nonempty_utf8_text(task_id: object) -> None:
    envelope = legacy_refund_envelope()
    envelope["task_id"] = task_id
    with pytest.raises(ServiceError) as error:
        validate_legacy_posting_envelope(envelope, expected_observation_id=OBSERVATION_ID)
    assert (error.value.status_code, error.value.error) == (502, "invalid_gateway_response")


@pytest.mark.parametrize("task_id", ["t-bank-only", "historique:économie"])
async def test_scheduler_delivers_historical_refund_through_strict_http_boundary(
    signer: GatewayRequestSigner, task_id: str
) -> None:
    pending = _historical_pending(task_id)
    refunded = {
        **pending,
        "observation_state": "refunded",
        "refund_receipt_event_id": 100,
        "refunded_at": "2026-10-18T12:34:56.123456Z",
        "result": {"escrow_status_at_commit": "released"},
    }
    page = {
        "items": [
            {"cursor": f"legacy:{OBSERVATION_ID}", "kind": "legacy", "identity": OBSERVATION_ID}
        ],
        "next_after": None,
    }
    graph = recovery_client_graph(
        signer,
        [
            httpx.Response(200, json=page),
            httpx.Response(202, json=pending),
            httpx.Response(200, json=refunded),
        ],
    )
    bank = AsyncMock()
    worker = PostingRecovery(store=graph.client, bank=bank, batch_size=2, diagnostic_limit=2)
    try:
        await worker.run_batch()
    finally:
        graph.client.close()
    bank.escrow_release.assert_awaited_once_with(
        escrow_id=pending["escrow_id"],
        recipient_account_id=pending["poster_id"],
        operation_id=pending["refund_operation_id"],
        source_reference=f"task:{task_id}:posting_refund:{pending['escrow_id']}",
    )
    bank.escrow_lock.assert_not_called()
    assert worker.diagnostics() == []
    assert len(graph.requests) == 3
