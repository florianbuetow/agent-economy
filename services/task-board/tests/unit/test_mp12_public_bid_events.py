"""MP-12 producer contract for public-safe bid submission events."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from task_board_service.services.task_db_client import TaskDbClient


class _TestSigner:
    def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
        request.headers["Authorization"] = f"Bearer {action}"
        return request


@pytest.mark.unit
def test_bid_submission_event_contains_no_sealed_bid_fields() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(201, json={})

    client = TaskDbClient(
        base_url="http://gateway.test",
        timeout_seconds=1,
        signer=_TestSigner(),  # type: ignore[arg-type]
    )
    client._client.close()  # pyright: ignore[reportPrivateUsage]
    client._client = httpx.Client(  # pyright: ignore[reportPrivateUsage]
        base_url="http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.insert_bid(
            {
                "bid_id": "bid-secret",
                "task_id": "task-public",
                "bidder_id": "agent-secret",
                "proposal": "proposal-secret",
                "amount": 73,
                "submitted_at": "2026-10-16T18:00:00Z",
            }
        )
    finally:
        client.close()

    body = captured[0]
    assert body["bid_id"] == "bid-secret"
    assert body["bidder_id"] == "agent-secret"
    assert body["amount"] == 73
    assert body["proposal"] == "proposal-secret"

    event = body["event"]
    assert event["event_type"] == "bid.submitted"
    assert event["task_id"] == "task-public"
    assert event["summary"] == "Bid submitted"
    assert event["agent_id"] == "agent-secret"
    assert json.loads(event["payload"]) == {}
