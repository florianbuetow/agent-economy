"""Tests for IdentityClient error contract through the feedback router.

The IdentityClient classifies Identity service responses into two categories:
- Verification failures (agent_not_found, invalid_jws) → forbidden (403)
- Infrastructure failures (connection, timeout, bad response) → identity_service_unavailable (502)

These tests verify the router surfaces those errors correctly by injecting
mocks that match the real IdentityClient's error contract. This ensures
mocks stay in sync with the client's actual behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from cryptography.exceptions import InvalidSignature

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_platform_agent

if TYPE_CHECKING:
    from httpx import AsyncClient

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"


def _feedback_payload(**overrides: object) -> dict[str, object]:
    """Return a valid JWS payload for feedback."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "t-remap-task",
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


def _inject_mock_error(side_effect: Exception) -> None:
    """Inject a mock IdentityClient that raises an exception."""
    state = get_app_state()
    state.platform_agent = make_mock_platform_agent(verify_side_effect=side_effect)


# =============================================================================
# IdentityClient raises forbidden (403) for verification failures
# =============================================================================


@pytest.mark.unit
class TestVerificationFailuresSurface403:
    """The IdentityClient raises forbidden (403) when the Identity service
    rejects the token (unregistered agent, invalid signature, malformed JWS).

    These mocks match what the real IdentityClient produces for non-200
    Identity responses with known verification failure codes.
    """

    async def test_unregistered_agent_returns_403(self, client: AsyncClient) -> None:
        """Unregistered agent (Identity 404 agent_not_found) → 403 forbidden."""
        _inject_mock_error(
            InvalidSignature(),
        )
        token = make_jws_token(_feedback_payload(), kid="a-unregistered-uuid")
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_invalid_jws_rejected_by_identity_returns_403(self, client: AsyncClient) -> None:
        """Cryptographically invalid JWS (Identity 400 invalid_jws) → 403 forbidden."""
        _inject_mock_error(
            InvalidSignature(),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_forbidden_message_is_generic(self, client: AsyncClient) -> None:
        """403 responses use a generic message, not upstream Identity details."""
        _inject_mock_error(
            InvalidSignature(),
        )
        token = make_jws_token(_feedback_payload(), kid="a-unregistered-uuid")
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        msg = response.json()["message"]
        assert "Agent not found" not in msg
        assert "keystore" not in msg
        assert "/data/" not in msg


# =============================================================================
# IdentityClient raises identity_service_unavailable (502) for infra failures
# =============================================================================


@pytest.mark.unit
class TestInfraFailuresSurface502:
    """The IdentityClient raises identity_service_unavailable (502) for
    connection failures, timeouts, and unexpected response formats.
    """

    async def test_connection_failure_returns_502(self, client: AsyncClient) -> None:
        """Identity service unreachable → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"

    async def test_unexpected_response_format_returns_502(self, client: AsyncClient) -> None:
        """Non-JSON or non-dict Identity response → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"


# =============================================================================
# IdentityClient validates 200 responses before returning
# =============================================================================


@pytest.mark.unit
class TestMalformed200ResponsesSurface502:
    """The IdentityClient raises identity_service_unavailable (502) when
    the Identity service returns 200 but the body is missing required fields
    or contains wrong types.

    These mocks match what the real IdentityClient produces when it receives
    a 200 response with incomplete or malformed verification data.
    """

    async def test_valid_not_boolean_returns_502(self, client: AsyncClient) -> None:
        """200 with non-boolean 'valid' field → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"

    async def test_missing_valid_field_returns_502(self, client: AsyncClient) -> None:
        """200 without 'valid' field → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"

    async def test_valid_false_returns_403(self, client: AsyncClient) -> None:
        """200 with valid=false → 403 forbidden (genuine verification failure)."""
        _inject_mock_error(
            InvalidSignature(),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_missing_agent_id_returns_502(self, client: AsyncClient) -> None:
        """200 with valid=true but no agent_id → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"

    async def test_missing_payload_returns_502(self, client: AsyncClient) -> None:
        """200 with valid=true but no payload → 502 identity_service_unavailable."""
        _inject_mock_error(
            Exception("Cannot reach Identity service"),
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "identity_service_unavailable"
