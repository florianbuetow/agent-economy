"""Tests for feedback router endpoints."""

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


@pytest.mark.unit
class TestPostFeedback:
    """Test POST /feedback."""

    async def test_submit_valid_feedback_returns_201(self, client: AsyncClient) -> None:
        """POST /feedback with a valid body returns 201."""
        payload = _feedback_payload()
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201

    async def test_submit_valid_feedback_returns_record(self, client: AsyncClient) -> None:
        """POST /feedback returns a feedback record with expected fields."""
        payload = _feedback_payload()
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        data = response.json()
        assert "feedback_id" in data
        assert data["feedback_id"].startswith("fb-")
        assert data["task_id"] == "task-1"
        assert data["from_agent_id"] == ALICE_ID
        assert data["to_agent_id"] == BOB_ID
        assert data["category"] == "delivery_quality"
        assert data["rating"] == "satisfied"
        assert data["comment"] == "Good work"
        assert "submitted_at" in data
        assert "visible" in data

    async def test_submit_missing_field_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback with a missing required field returns 400 MISSING_FIELD."""
        payload = _feedback_payload()
        del payload["task_id"]
        _inject_mock(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "MISSING_FIELD"

    async def test_submit_without_content_type_returns_415(self, client: AsyncClient) -> None:
        """POST /feedback without Content-Type: application/json returns 415."""
        response = await client.post(
            "/feedback",
            content=json.dumps(_token_body()).encode(),
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        data = response.json()
        assert data["error"] == "UNSUPPORTED_MEDIA_TYPE"

    async def test_submit_invalid_json_returns_400(self, client: AsyncClient) -> None:
        """POST /feedback with invalid JSON returns 400 INVALID_JSON."""
        response = await client.post(
            "/feedback",
            content=b"not valid json {{{",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "INVALID_JSON"

    async def test_submit_duplicate_returns_409(self, client: AsyncClient) -> None:
        """POST /feedback with a duplicate (task, from, to) returns 409."""
        payload = _feedback_payload()
        _inject_mock(payload)
        await client.post("/feedback", json=_token_body(payload))
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 409
        data = response.json()
        assert data["error"] == "FEEDBACK_EXISTS"


@pytest.mark.unit
class TestGetFeedbackById:
    """Test GET /feedback/{feedback_id}."""

    async def test_get_sealed_feedback_returns_404(self, client: AsyncClient) -> None:
        """GET /feedback/{id} for a sealed (not yet revealed) record returns 404."""
        payload = _feedback_payload()
        _inject_mock(payload)
        post_response = await client.post("/feedback", json=_token_body(payload))
        feedback_id = post_response.json()["feedback_id"]

        response = await client.get(f"/feedback/{feedback_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["error"] == "FEEDBACK_NOT_FOUND"

    async def test_get_visible_feedback_returns_200(self, client: AsyncClient) -> None:
        """GET /feedback/{id} for a visible record returns 200."""
        # Submit A->B
        payload_ab = _feedback_payload(from_agent_id=ALICE_ID, to_agent_id=BOB_ID)
        _inject_mock(payload_ab, agent_id=ALICE_ID)
        await client.post("/feedback", json=_token_body(payload_ab, kid=ALICE_ID))

        # Submit B->A (mutual reveal)
        payload_ba = _feedback_payload(from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
        _inject_mock(payload_ba, agent_id=BOB_ID)
        post_response = await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))
        feedback_id = post_response.json()["feedback_id"]

        response = await client.get(f"/feedback/{feedback_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["feedback_id"] == feedback_id

    async def test_get_nonexistent_feedback_returns_404(self, client: AsyncClient) -> None:
        """GET /feedback/{id} for a nonexistent ID returns 404."""
        response = await client.get("/feedback/fb-nonexistent")
        assert response.status_code == 404


@pytest.mark.unit
class TestGetFeedbackForTask:
    """Test GET /feedback/task/{task_id}."""

    async def test_get_task_feedback_returns_list(self, client: AsyncClient) -> None:
        """GET /feedback/task/{task_id} returns a list."""
        response = await client.get("/feedback/task/task-1")
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "task-1"
        assert isinstance(data["feedback"], list)

    async def test_get_task_feedback_empty_for_new_task(self, client: AsyncClient) -> None:
        """GET /feedback/task/{task_id} returns empty list for unknown task."""
        response = await client.get("/feedback/task/task-unknown")
        data = response.json()
        assert data["feedback"] == []

    async def test_get_task_feedback_returns_visible_only(self, client: AsyncClient) -> None:
        """GET /feedback/task/{task_id} returns only visible feedback."""
        # Submit one direction (sealed)
        payload_ab = _feedback_payload(from_agent_id=ALICE_ID, to_agent_id=BOB_ID)
        _inject_mock(payload_ab, agent_id=ALICE_ID)
        await client.post("/feedback", json=_token_body(payload_ab, kid=ALICE_ID))

        response = await client.get("/feedback/task/task-1")
        data = response.json()
        assert data["feedback"] == []

        # Submit reverse direction (reveals both)
        payload_ba = _feedback_payload(from_agent_id=BOB_ID, to_agent_id=ALICE_ID)
        _inject_mock(payload_ba, agent_id=BOB_ID)
        await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

        response = await client.get("/feedback/task/task-1")
        data = response.json()
        assert len(data["feedback"]) == 2


@pytest.mark.unit
class TestGetFeedbackForAgent:
    """Test GET /feedback/agent/{agent_id}."""

    async def test_get_agent_feedback_returns_list(self, client: AsyncClient) -> None:
        """GET /feedback/agent/{agent_id} returns a list."""
        response = await client.get(f"/feedback/agent/{BOB_ID}")
        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == BOB_ID
        assert isinstance(data["feedback"], list)

    async def test_get_agent_feedback_empty_for_unknown_agent(self, client: AsyncClient) -> None:
        """GET /feedback/agent/{agent_id} returns empty list for unknown agent."""
        response = await client.get("/feedback/agent/agent-unknown")
        data = response.json()
        assert data["feedback"] == []


@pytest.mark.unit
class TestMethodNotAllowed:
    """Test that wrong HTTP methods return 405."""

    async def test_put_feedback_returns_405(self, client: AsyncClient) -> None:
        """PUT /feedback returns 405."""
        response = await client.put("/feedback", json=_token_body())
        assert response.status_code == 405
        data = response.json()
        assert data["error"] == "METHOD_NOT_ALLOWED"

    async def test_delete_feedback_returns_405(self, client: AsyncClient) -> None:
        """DELETE /feedback/fb-123 returns 405."""
        response = await client.delete("/feedback/fb-123")
        assert response.status_code == 405

    async def test_post_health_returns_405(self, client: AsyncClient) -> None:
        """POST /health returns 405."""
        response = await client.post("/health")
        assert response.status_code == 405
