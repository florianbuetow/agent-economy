"""FeedbackDbClient defers the sealed-feedback reveal decision to the gateway.

The client used to read the reverse pair first and then tell the gateway which row
to reveal. That prior read races: two concurrent counter-feedbacks both see "no
reverse yet" and both persist sealed. The gateway now decides inside its write
transaction, so the client must submit blind and trust the response's `visible`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from reputation_service.services.exceptions import DuplicateFeedbackError
from reputation_service.services.feedback_db_client import FeedbackDbClient

TASK_ID = "t-1"
ALICE = "a-alice"
BOB = "a-bob"


class _MockGateway:
    """Minimal stand-in for the gateway's atomic reveal policy."""

    def __init__(self, *, duplicate: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict[str, Any]] = []
        self._pairs: set[tuple[str, str, str]] = set()
        self._duplicate = duplicate

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "POST":
            return httpx.Response(200, json={"feedback": []})

        body = json.loads(request.content)
        self.bodies.append(body)
        if self._duplicate:
            return httpx.Response(409, json={"error": "feedback_exists"})

        key = (body["task_id"], body["from_agent_id"], body["to_agent_id"])
        reverse = (body["task_id"], body["to_agent_id"], body["from_agent_id"])
        self._pairs.add(key)
        visible = bool(body.get("force_visible", False)) or reverse in self._pairs
        return httpx.Response(
            201,
            json={
                "feedback_id": body["feedback_id"],
                "visible": visible,
                "event_id": len(self.bodies),
            },
        )


def _client(gateway: _MockGateway) -> FeedbackDbClient:
    client = FeedbackDbClient(base_url="http://gateway.test", timeout_seconds=5)
    client._client = httpx.Client(
        base_url="http://gateway.test",
        transport=httpx.MockTransport(gateway.handler),
    )
    return client


def _submit(
    client: FeedbackDbClient,
    from_agent_id: str,
    to_agent_id: str,
    *,
    category: str,
    force_visible: bool = False,
) -> Any:
    return client.insert_feedback(
        task_id=TASK_ID,
        from_agent_id=from_agent_id,
        to_agent_id=to_agent_id,
        category=category,
        rating="satisfied",
        comment="ok",
        force_visible=force_visible,
    )


@pytest.mark.unit
class TestFeedbackDbClientReveal:
    def test_first_submission_reports_sealed(self) -> None:
        gateway = _MockGateway()
        client = _client(gateway)

        record = _submit(client, ALICE, BOB, category="delivery_quality")

        assert record.visible is False
        client.close()

    def test_second_submission_reports_revealed(self) -> None:
        gateway = _MockGateway()
        client = _client(gateway)

        first = _submit(client, ALICE, BOB, category="delivery_quality")
        second = _submit(client, BOB, ALICE, category="spec_quality")

        assert first.visible is False
        assert second.visible is True
        client.close()

    def test_visible_is_taken_from_the_gateway_response(self) -> None:
        """The client must not recompute visibility from its own stale view."""
        gateway = _MockGateway()
        client = _client(gateway)

        _submit(client, ALICE, BOB, category="delivery_quality")
        record = _submit(client, BOB, ALICE, category="spec_quality")

        assert record.visible is True
        client.close()

    def test_no_prior_read_before_the_write(self) -> None:
        """Exactly one request: the POST. The reverse-pair GET is gone."""
        gateway = _MockGateway()
        client = _client(gateway)

        _submit(client, ALICE, BOB, category="delivery_quality")

        assert [r.method for r in gateway.requests] == ["POST"]
        assert str(gateway.requests[0].url).endswith("/reputation/feedback")
        client.close()

    def test_payload_carries_force_visible_and_no_reveal_directives(self) -> None:
        gateway = _MockGateway()
        client = _client(gateway)

        _submit(client, ALICE, BOB, category="delivery_quality", force_visible=True)

        body = gateway.bodies[0]
        assert body["force_visible"] is True
        assert "reveal_reverse" not in body
        assert "reverse_feedback_id" not in body
        client.close()

    def test_force_visible_false_is_sent_explicitly(self) -> None:
        """The policy flag is always stated; it never collapses into 'reverse exists'."""
        gateway = _MockGateway()
        client = _client(gateway)

        _submit(client, ALICE, BOB, category="delivery_quality")
        _submit(client, BOB, ALICE, category="spec_quality")

        assert gateway.bodies[0]["force_visible"] is False
        # The second submission is revealed by the reverse pair, not by the flag.
        assert gateway.bodies[1]["force_visible"] is False
        client.close()

    def test_platform_feedback_is_visible_immediately(self) -> None:
        gateway = _MockGateway()
        client = _client(gateway)

        record = _submit(client, ALICE, BOB, category="delivery_quality", force_visible=True)

        assert record.visible is True
        client.close()

    def test_duplicate_still_raises(self) -> None:
        gateway = _MockGateway(duplicate=True)
        client = _client(gateway)

        with pytest.raises(DuplicateFeedbackError):
            _submit(client, ALICE, BOB, category="delivery_quality")
        client.close()
