"""MP-12 producer contract for public-safe sealed-feedback events."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from reputation_service.services.feedback_db_client import FeedbackDbClient


class _TestSigner:
    def sign_request(self, request: httpx.Request, *, action: str) -> httpx.Request:
        request.headers["Authorization"] = f"Bearer {action}"
        return request


@pytest.mark.unit
def test_feedback_submission_event_contains_no_party_or_quality_fields() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(201, json={"visible": False})

    client = FeedbackDbClient(
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
        client.insert_feedback(
            task_id="task-public",
            from_agent_id="agent-secret-from",
            to_agent_id="agent-secret-to",
            category="spec_quality",
            rating="extremely_satisfied",
            comment="comment-secret",
            force_visible=False,
        )
    finally:
        client.close()

    body = captured[0]
    assert body["from_agent_id"] == "agent-secret-from"
    assert body["to_agent_id"] == "agent-secret-to"
    assert body["category"] == "spec_quality"
    assert body["rating"] == "extremely_satisfied"
    assert body["comment"] == "comment-secret"

    event = body["event"]
    assert event["event_type"] == "feedback.submitted"
    assert event["task_id"] == "task-public"
    assert event["summary"] == "Feedback submitted"
    assert "agent_id" not in event
    assert json.loads(event["payload"]) == {}
