"""Production GatewayClient provenance contract for MP-07 settlements."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from service_clients.gateway import GatewayClient

if TYPE_CHECKING:
    from typing import Any

pytestmark = pytest.mark.unit


class _TestSigner:
    def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
        assert action == "gateway.bank.write"
        request.headers["Authorization"] = "Bearer test"
        return request


async def test_release_event_emits_canonical_source_with_optional_task_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Capture the exact settlement event produced by the shared production client."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"outcome": "captured"})

    client = GatewayClient(
        base_url="http://gateway.invalid",
        timeout_seconds=1,
        signer=_TestSigner(),  # type: ignore[arg-type]
    )
    await client.connection.aclose()
    monkeypatch.setattr(
        client,
        "_client",
        httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://gateway.invalid",
        ),
    )
    try:
        await client.escrow_release(
            escrow_id="esc-1",
            recipient_account_id="a-worker",
            tx_id="tx-1",
            resolved_at="2099-01-01T00:00:03Z",
            source_reference="task:t-1:approved",
            operation_id="op-00000000-0000-4000-8000-000000000001",
        )
    finally:
        await client.close()

    event = captured["event"]
    assert event["event_source"] == "central-bank"
    assert event["event_type"] == "escrow.released"
    assert "task_id" not in event
    assert event["agent_id"] == "a-worker"
