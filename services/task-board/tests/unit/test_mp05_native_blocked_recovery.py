"""A validated native blocked acknowledgment remains visible to recovery operators."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from task_board_service.services.posting_recovery import PostingRecovery
from tests.unit.test_mp05_posting_recovery_client import recovery_client_graph, signer

if TYPE_CHECKING:
    from service_auth.gateway import GatewayRequestSigner

pytestmark = pytest.mark.unit
__all__ = ["signer"]


async def test_native_blocked_acknowledgment_is_recorded_through_real_client(
    signer: GatewayRequestSigner,
) -> None:
    operation_id = "op-00000000-0000-4000-8000-000000000001"
    cursor = f"native:{operation_id}"
    envelope = {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": "t-00000000-0000-4000-8000-000000000002",
        "request_sha256": "a" * 64,
        "publication_state": "blocked_integrity",
        "lock_operation_id": "op-00000000-0000-4000-8000-000000000003",
        "escrow_id": None,
        "admitted_at": "2026-10-18T12:34:56.123456Z",
        "published_at": None,
        "result": None,
        "error": {
            "error": "publication_integrity_blocked",
            "message": "Publication evidence requires reconciliation",
            "details": {"reason": "receipt_invalid"},
        },
    }
    page = {
        "items": [{"cursor": cursor, "kind": "native", "identity": operation_id}],
        "next_after": None,
    }
    blocked = {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": {"publication": envelope},
    }
    graph = recovery_client_graph(
        signer, [httpx.Response(200, json=page), httpx.Response(409, json=blocked)]
    )
    bank = AsyncMock()
    recovery = PostingRecovery(store=graph.client, bank=bank, batch_size=2, diagnostic_limit=2)
    try:
        await recovery.run_batch()
    finally:
        graph.client.close()
    assert recovery.diagnostics() == [{"cursor": cursor, "error": "publication_integrity_blocked"}]
    assert len(graph.requests) == 2
    assert bank.mock_calls == []
