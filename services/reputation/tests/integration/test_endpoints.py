"""Integration tests for the reputation service endpoints.

These tests exercise the full request/response cycle through the app,
testing multi-step workflows that span multiple endpoints.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_identity_client

if TYPE_CHECKING:
    from httpx import AsyncClient

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"


def _feedback_payload(**overrides: object) -> dict[str, object]:
    """Return a valid JWS payload for feedback."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "task-1",
        "from_agent_id": ALICE_ID,
        "to_agent_id": BOB_ID,
        "category": "delivery_quality",
        "rating": "satisfied",
        "comment": "Good work",
    }
    base.update(overrides)
    return base


def _token_body(
    payload: dict[str, object] | None = None,
    kid: str = ALICE_ID,
) -> dict[str, object]:
    """Wrap a payload in a JWS token body for POST /feedback."""
    if payload is None:
        payload = _feedback_payload()
    return {"token": make_jws_token(payload, kid=kid)}


def _mock_verify_ok(
    agent_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Return a successful verify_jws response."""
    return {"valid": True, "agent_id": agent_id, "payload": payload}


def _inject_mock(payload: dict[str, object] | None = None, agent_id: str = ALICE_ID) -> None:
    """Inject a mock IdentityClient returning success for the given payload."""
    if payload is None:
        payload = _feedback_payload()
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_response=_mock_verify_ok(agent_id, payload),
    )


@pytest.mark.integration
class TestSubmitAndRetrieveFeedbackWorkflow:
    """Test the full submit-and-retrieve feedback lifecycle."""

    async def test_sealed_feedback_becomes_visible_after_mutual_submission(
        self, client: AsyncClient
    ) -> None:
        """Submit feedback A->B, verify sealed, then B->A reveals both."""
        # Step 1: Submit feedback A->B (first direction, should be sealed)
        payload_ab = _feedback_payload()
        _inject_mock(payload_ab)
        resp1 = await client.post("/feedback", json=_token_body(payload_ab))
        assert resp1.status_code == 201
        data1 = resp1.json()
        assert data1["visible"] is False
        feedback_id_1 = data1["feedback_id"]

        # Step 2: GET /feedback/{id} for sealed feedback returns 404
        resp2 = await client.get(f"/feedback/{feedback_id_1}")
        assert resp2.status_code == 404
        assert resp2.json()["error"] == "FEEDBACK_NOT_FOUND"

        # Step 3: Submit reverse feedback B->A (mutual reveal)
        payload_ba = _feedback_payload(from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
        _inject_mock(payload_ba, agent_id=BOB_ID)
        resp3 = await client.post(
            "/feedback",
            json=_token_body(payload_ba, kid=BOB_ID),
        )
        assert resp3.status_code == 201
        data3 = resp3.json()
        assert data3["visible"] is True
        feedback_id_2 = data3["feedback_id"]

        # Step 4: GET /feedback/{id} for previously sealed feedback (now visible)
        resp4 = await client.get(f"/feedback/{feedback_id_1}")
        assert resp4.status_code == 200
        assert resp4.json()["visible"] is True

        # Step 5: GET /feedback/task/task-1 returns both records
        resp5 = await client.get("/feedback/task/task-1")
        assert resp5.status_code == 200
        task_data = resp5.json()
        assert task_data["task_id"] == "task-1"
        assert len(task_data["feedback"]) == 2
        returned_ids = {fb["feedback_id"] for fb in task_data["feedback"]}
        assert feedback_id_1 in returned_ids
        assert feedback_id_2 in returned_ids
        for fb in task_data["feedback"]:
            assert fb["visible"] is True

        # Step 6: GET /feedback/agent/agent-b returns 1 visible record (to_agent_id)
        resp6 = await client.get(f"/feedback/agent/{BOB_ID}")
        assert resp6.status_code == 200
        agent_data = resp6.json()
        assert agent_data["agent_id"] == BOB_ID
        assert len(agent_data["feedback"]) == 1
        assert agent_data["feedback"][0]["to_agent_id"] == BOB_ID


@pytest.mark.integration
class TestErrorResponsesWorkflow:
    """Test error handling across multiple scenarios."""

    async def test_missing_field_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback with missing required field returns 400 MISSING_FIELD."""
        payload = _feedback_payload()
        del payload["task_id"]
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "MISSING_FIELD"
        assert "message" in data
        assert "details" in data

    async def test_invalid_category_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback with invalid category returns 400 INVALID_CATEGORY."""
        payload = _feedback_payload(category="bogus_category")
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "INVALID_CATEGORY"
        assert "message" in data
        assert "details" in data

    async def test_self_feedback_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback where from_agent == to_agent returns 400 SELF_FEEDBACK."""
        payload = _feedback_payload(from_agent_id=ALICE_ID, to_agent_id=ALICE_ID)
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "SELF_FEEDBACK"
        assert "message" in data
        assert "details" in data

    async def test_duplicate_feedback_returns_409(self, client: AsyncClient) -> None:
        """POST /feedback twice with same (task, from, to) returns 409 FEEDBACK_EXISTS."""
        payload = _feedback_payload()
        _inject_mock(payload)
        resp1 = await client.post("/feedback", json=_token_body(payload))
        assert resp1.status_code == 201

        resp2 = await client.post("/feedback", json=_token_body(payload))
        assert resp2.status_code == 409
        data = resp2.json()
        assert data["error"] == "FEEDBACK_EXISTS"
        assert "message" in data
        assert "details" in data

    async def test_wrong_content_type_returns_415(self, client: AsyncClient) -> None:
        """POST /feedback with wrong Content-Type returns 415."""
        response = await client.post(
            "/feedback",
            content=json.dumps(_token_body()).encode(),
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        data = response.json()
        assert data["error"] == "UNSUPPORTED_MEDIA_TYPE"
        assert "message" in data
        assert "details" in data

    async def test_invalid_json_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback with invalid JSON body returns 400 INVALID_JSON."""
        response = await client.post(
            "/feedback",
            content=b"not valid json {{{",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "INVALID_JSON"
        assert "message" in data
        assert "details" in data


@pytest.mark.integration
class TestHealthCheckWorkflow:
    """Test health endpoint reflects feedback count including sealed."""

    async def test_health_tracks_total_feedback_including_sealed(self, client: AsyncClient) -> None:
        """Health endpoint total_feedback counts all feedback, including sealed."""
        # Step 1: GET /health shows total_feedback=0
        resp1 = await client.get("/health")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["status"] == "ok"
        assert data1["total_feedback"] == 0
        assert "uptime_seconds" in data1
        assert "started_at" in data1

        # Step 2: Submit one feedback (sealed)
        payload = _feedback_payload()
        _inject_mock(payload)
        resp2 = await client.post("/feedback", json=_token_body(payload))
        assert resp2.status_code == 201

        # Step 3: GET /health shows total_feedback=1
        resp3 = await client.get("/health")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["total_feedback"] == 1


@pytest.mark.integration
class TestMethodNotAllowed:
    """Test that wrong HTTP methods return 405 METHOD_NOT_ALLOWED."""

    async def test_put_feedback_returns_405(self, client: AsyncClient) -> None:
        """PUT /feedback returns 405 METHOD_NOT_ALLOWED."""
        response = await client.put("/feedback", json=_token_body())
        assert response.status_code == 405
        data = response.json()
        assert data["error"] == "METHOD_NOT_ALLOWED"
        assert "message" in data
        assert "details" in data

    async def test_delete_health_returns_405(self, client: AsyncClient) -> None:
        """DELETE /health returns 405 METHOD_NOT_ALLOWED."""
        response = await client.delete("/health")
        assert response.status_code == 405
        data = response.json()
        assert data["error"] == "METHOD_NOT_ALLOWED"
        assert "message" in data
        assert "details" in data


@pytest.mark.integration
class TestCommentHandling:
    """Test comment field handling in feedback submissions."""

    async def test_feedback_with_comment(self, client: AsyncClient) -> None:
        """Submit feedback with a comment, verify it appears in the response."""
        payload = _feedback_payload(comment="Excellent delivery")
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert data["comment"] == "Excellent delivery"

    async def test_feedback_without_comment(self, client: AsyncClient) -> None:
        """Submit feedback without a comment, verify comment is null."""
        payload = _feedback_payload()
        del payload["comment"]
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert data["comment"] is None
