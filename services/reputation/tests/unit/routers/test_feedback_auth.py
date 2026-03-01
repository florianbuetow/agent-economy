"""Authentication acceptance tests for POST /feedback.

Covers all 39 test cases from the reputation-service-auth-tests.md spec:
- Category 1: JWS Token Validation (AUTH-01 to AUTH-08)
- Category 2: JWS Payload Validation (AUTH-09 to AUTH-11)
- Category 3: Authorization / Signer Matching (AUTH-12 to AUTH-14)
- Category 4: Identity Service Unavailability (AUTH-15 to AUTH-16)
- Category 5: GET Endpoints Remain Public (PUB-01 to PUB-04)
- Category 6: Error Precedence (PREC-01 to PREC-07)
- Category 7: Existing Validations Through JWS (VJWS-01 to VJWS-09)
- Category 8: Cross-Cutting Security (SEC-AUTH-01 to SEC-AUTH-03)
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from reputation_service.core.state import get_app_state
from tests.helpers import make_jws_token, make_mock_identity_client

if TYPE_CHECKING:
    from httpx import AsyncClient

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"
CAROL_ID = "a-carol-uuid"


def _feedback_payload(**overrides: object) -> dict[str, object]:
    """Return a valid JWS payload for feedback."""
    base: dict[str, object] = {
        "action": "submit_feedback",
        "task_id": "t-task-1",
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


def _inject_mock_ok(
    payload: dict[str, object] | None = None,
    agent_id: str = ALICE_ID,
) -> None:
    """Inject a mock IdentityClient returning success."""
    if payload is None:
        payload = _feedback_payload()
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_response=_mock_verify_ok(agent_id, payload),
    )


def _inject_mock_error(side_effect: Exception) -> None:
    """Inject a mock IdentityClient that raises an exception."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(verify_side_effect=side_effect)


def _inject_mock_invalid_sig() -> None:
    """Inject a mock IdentityClient returning valid=false (tampered/unregistered)."""
    state = get_app_state()
    state.identity_client = make_mock_identity_client(
        verify_side_effect=ServiceError("FORBIDDEN", "Signature verification failed", 403, {}),
    )


# =============================================================================
# Category 1: JWS Token Validation (AUTH-01 to AUTH-08)
# =============================================================================


@pytest.mark.unit
class TestJWSTokenValidation:
    """Category 1: JWS Token Validation."""

    async def test_auth_01_valid_jws_submits_feedback(self, client: AsyncClient) -> None:
        """AUTH-01: Valid JWS submits feedback successfully."""
        payload = _feedback_payload()
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        data = response.json()
        assert "feedback_id" in data
        assert data["feedback_id"].startswith("fb-")
        assert data["task_id"] == "t-task-1"
        assert data["from_agent_id"] == ALICE_ID
        assert data["to_agent_id"] == BOB_ID
        assert data["category"] == "delivery_quality"
        assert data["rating"] == "satisfied"
        assert data["comment"] == "Good work"
        assert "submitted_at" in data
        assert "visible" in data

    async def test_auth_02_missing_token_field(self, client: AsyncClient) -> None:
        """AUTH-02: Missing token field returns 400 INVALID_JWS."""
        response = await client.post(
            "/feedback",
            json={
                "task_id": "t-xxx",
                "from_agent_id": ALICE_ID,
                "to_agent_id": BOB_ID,
                "category": "delivery_quality",
                "rating": "satisfied",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_03_token_is_null(self, client: AsyncClient) -> None:
        """AUTH-03: token is null returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": None})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_04_token_is_integer(self, client: AsyncClient) -> None:
        """AUTH-04: token is integer returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": 12345})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_04_token_is_list(self, client: AsyncClient) -> None:
        """AUTH-04: token is list returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": ["eyJ..."]})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_04_token_is_object(self, client: AsyncClient) -> None:
        """AUTH-04: token is object returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": {"jws": "eyJ..."}})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_04_token_is_boolean(self, client: AsyncClient) -> None:
        """AUTH-04: token is boolean returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": True})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_05_token_is_empty_string(self, client: AsyncClient) -> None:
        """AUTH-05: token is empty string returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": ""})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_06_malformed_not_jws(self, client: AsyncClient) -> None:
        """AUTH-06: Malformed JWS (no dots) returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": "not-a-jws-at-all"})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_06_malformed_two_parts(self, client: AsyncClient) -> None:
        """AUTH-06: Malformed JWS (two parts) returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": "only.two-parts"})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_06_malformed_four_parts(self, client: AsyncClient) -> None:
        """AUTH-06: Malformed JWS (four parts) returns 400 INVALID_JWS."""
        response = await client.post("/feedback", json={"token": "four.parts.is.wrong"})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_auth_07_tampered_payload(self, client: AsyncClient) -> None:
        """AUTH-07: JWS with tampered payload returns 403 FORBIDDEN."""
        _inject_mock_invalid_sig()
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_08_unregistered_agent(self, client: AsyncClient) -> None:
        """AUTH-08: JWS signed by unregistered agent returns 403 FORBIDDEN."""
        _inject_mock_invalid_sig()
        token = make_jws_token(_feedback_payload(), kid="a-unregistered-uuid")
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"


# =============================================================================
# Category 2: JWS Payload Validation (AUTH-09 to AUTH-11)
# =============================================================================


@pytest.mark.unit
class TestJWSPayloadValidation:
    """Category 2: JWS Payload Validation."""

    async def test_auth_09_missing_action(self, client: AsyncClient) -> None:
        """AUTH-09: Missing action in payload returns 400 INVALID_PAYLOAD."""
        payload = _feedback_payload()
        del payload["action"]
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_auth_10_wrong_action(self, client: AsyncClient) -> None:
        """AUTH-10: Wrong action value returns 400 INVALID_PAYLOAD."""
        payload = _feedback_payload(action="escrow_lock")
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_auth_11_null_action(self, client: AsyncClient) -> None:
        """AUTH-11: action is null returns 400 INVALID_PAYLOAD."""
        payload = _feedback_payload(action=None)
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"


# =============================================================================
# Category 3: Authorization / Signer Matching (AUTH-12 to AUTH-14)
# =============================================================================


@pytest.mark.unit
class TestSignerMatching:
    """Category 3: Authorization (Signer Matching)."""

    async def test_auth_12_signer_matches(self, client: AsyncClient) -> None:
        """AUTH-12: Signer matches from_agent_id — success."""
        payload = _feedback_payload()
        _inject_mock_ok(payload, agent_id=ALICE_ID)
        response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
        assert response.status_code == 201
        assert response.json()["from_agent_id"] == ALICE_ID

    async def test_auth_13_impersonation_rejected(self, client: AsyncClient) -> None:
        """AUTH-13: Signer does NOT match from_agent_id — 403 FORBIDDEN."""
        # Alice signs but claims to be Carol
        payload = _feedback_payload(from_agent_id=CAROL_ID)
        # Identity returns alice as the signer
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response=_mock_verify_ok(ALICE_ID, payload),
        )
        response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_auth_14_impersonate_nonexistent(self, client: AsyncClient) -> None:
        """AUTH-14: Signer impersonates non-existent agent — 403 FORBIDDEN."""
        payload = _feedback_payload(from_agent_id="a-nonexistent-uuid")
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response=_mock_verify_ok(ALICE_ID, payload),
        )
        response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"


# =============================================================================
# Category 4: Identity Service Unavailability (AUTH-15 to AUTH-16)
# =============================================================================


@pytest.mark.unit
class TestIdentityServiceUnavailability:
    """Category 4: Identity Service Unavailability."""

    async def test_auth_15_identity_down(self, client: AsyncClient) -> None:
        """AUTH-15: Identity service is down returns 502 IDENTITY_SERVICE_UNAVAILABLE."""
        _inject_mock_error(
            ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "Cannot reach Identity service", 502, {})
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    async def test_auth_16_identity_unexpected_response(self, client: AsyncClient) -> None:
        """AUTH-16: Identity returns unexpected response — 502 IDENTITY_SERVICE_UNAVAILABLE."""
        _inject_mock_error(
            ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Identity service returned unexpected response (status 500)",
                502,
                {},
            )
        )
        token = make_jws_token(_feedback_payload())
        response = await client.post("/feedback", json={"token": token})
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"


# =============================================================================
# Category 5: GET Endpoints Remain Public (PUB-01 to PUB-04)
# =============================================================================


@pytest.mark.unit
class TestGetEndpointsPublic:
    """Category 5: GET Endpoints Remain Public."""

    async def test_pub_01_get_feedback_by_id(self, client: AsyncClient) -> None:
        """PUB-01: GET /feedback/{feedback_id} requires no authentication."""
        # Submit feedback and reveal it
        payload_ab = _feedback_payload()
        _inject_mock_ok(payload_ab, agent_id=ALICE_ID)
        resp1 = await client.post("/feedback", json=_token_body(payload_ab))
        feedback_id = resp1.json()["feedback_id"]

        # Submit counterpart to reveal
        payload_ba = _feedback_payload(
            from_agent_id=BOB_ID,
            to_agent_id=ALICE_ID,
            task_id="t-task-1",
        )
        _inject_mock_ok(payload_ba, agent_id=BOB_ID)
        await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

        # GET with no auth
        response = await client.get(f"/feedback/{feedback_id}")
        assert response.status_code == 200
        assert response.json()["feedback_id"] == feedback_id

    async def test_pub_02_get_task_feedback(self, client: AsyncClient) -> None:
        """PUB-02: GET /feedback/task/{task_id} requires no authentication."""
        # Submit and reveal feedback
        payload_ab = _feedback_payload()
        _inject_mock_ok(payload_ab, agent_id=ALICE_ID)
        await client.post("/feedback", json=_token_body(payload_ab))

        payload_ba = _feedback_payload(
            from_agent_id=BOB_ID,
            to_agent_id=ALICE_ID,
            task_id="t-task-1",
        )
        _inject_mock_ok(payload_ba, agent_id=BOB_ID)
        await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

        response = await client.get("/feedback/task/t-task-1")
        assert response.status_code == 200
        assert len(response.json()["feedback"]) > 0

    async def test_pub_03_get_agent_feedback(self, client: AsyncClient) -> None:
        """PUB-03: GET /feedback/agent/{agent_id} requires no authentication."""
        # Submit and reveal feedback
        payload_ab = _feedback_payload()
        _inject_mock_ok(payload_ab, agent_id=ALICE_ID)
        await client.post("/feedback", json=_token_body(payload_ab))

        payload_ba = _feedback_payload(
            from_agent_id=BOB_ID,
            to_agent_id=ALICE_ID,
            task_id="t-task-1",
        )
        _inject_mock_ok(payload_ba, agent_id=BOB_ID)
        await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))

        response = await client.get(f"/feedback/agent/{BOB_ID}")
        assert response.status_code == 200
        assert len(response.json()["feedback"]) > 0

    async def test_pub_04_get_health(self, client: AsyncClient) -> None:
        """PUB-04: GET /health requires no authentication."""
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


# =============================================================================
# Category 6: Error Precedence (PREC-01 to PREC-07)
# =============================================================================


@pytest.mark.unit
class TestErrorPrecedence:
    """Category 6: Error Precedence."""

    async def test_prec_01_content_type_before_token(self, client: AsyncClient) -> None:
        """PREC-01: Content-Type checked before token validation."""
        response = await client.post(
            "/feedback",
            content=json.dumps({"token": "invalid"}).encode(),
            headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"

    async def test_prec_02_body_size_before_token(self, client: AsyncClient) -> None:
        """PREC-02: Body size checked before token validation."""
        # ~2MB body
        large_body = json.dumps({"token": "x" * (2 * 1024 * 1024)}).encode()
        response = await client.post(
            "/feedback",
            content=large_body,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["error"] == "PAYLOAD_TOO_LARGE"

    async def test_prec_03_json_before_token(self, client: AsyncClient) -> None:
        """PREC-03: JSON parsing checked before token validation."""
        response = await client.post(
            "/feedback",
            content=b"{not json",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JSON"

    async def test_prec_04_token_before_payload(self, client: AsyncClient) -> None:
        """PREC-04: Token validation checked before payload validation."""
        response = await client.post("/feedback", json={"token": 12345})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"

    async def test_prec_05_action_before_signer(self, client: AsyncClient) -> None:
        """PREC-05: Payload action checked before signer matching."""
        # Wrong action AND signer mismatch
        payload = _feedback_payload(action="wrong_action", from_agent_id=BOB_ID)
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response=_mock_verify_ok(ALICE_ID, payload),
        )
        response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    async def test_prec_06_signer_before_feedback_validation(self, client: AsyncClient) -> None:
        """PREC-06: Signer matching checked before feedback field validation."""
        # Signer mismatch AND invalid rating
        payload = _feedback_payload(from_agent_id=CAROL_ID, rating="invalid_value")
        state = get_app_state()
        state.identity_client = make_mock_identity_client(
            verify_response=_mock_verify_ok(ALICE_ID, payload),
        )
        response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_prec_07_identity_unavailable_before_payload(self, client: AsyncClient) -> None:
        """PREC-07: Identity unavailability checked before payload validation."""
        # Identity down AND wrong action
        _inject_mock_error(
            ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "Cannot reach Identity service", 502, {})
        )
        payload = _feedback_payload(action="wrong_action")
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 502
        assert response.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"


# =============================================================================
# Category 7: Existing Validations Through JWS (VJWS-01 to VJWS-09)
# =============================================================================


@pytest.mark.unit
class TestExistingValidationsThroughJWS:
    """Category 7: Existing Validations Through JWS."""

    async def test_vjws_01_missing_feedback_fields(self, client: AsyncClient) -> None:
        """VJWS-01: Missing feedback fields in JWS payload returns 400 MISSING_FIELD."""
        payload: dict[str, object] = {
            "action": "submit_feedback",
            "from_agent_id": ALICE_ID,
        }
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "MISSING_FIELD"

    async def test_vjws_02_invalid_rating(self, client: AsyncClient) -> None:
        """VJWS-02: Invalid rating in JWS payload returns 400 INVALID_RATING."""
        payload = _feedback_payload(rating="excellent")
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_RATING"

    async def test_vjws_03_invalid_category(self, client: AsyncClient) -> None:
        """VJWS-03: Invalid category in JWS payload returns 400 INVALID_CATEGORY."""
        payload = _feedback_payload(category="timeliness")
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_CATEGORY"

    async def test_vjws_04_self_feedback(self, client: AsyncClient) -> None:
        """VJWS-04: Self-feedback in JWS payload returns 400 SELF_FEEDBACK."""
        payload = _feedback_payload(from_agent_id=ALICE_ID, to_agent_id=ALICE_ID)
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "SELF_FEEDBACK"

    async def test_vjws_05_comment_too_long(self, client: AsyncClient) -> None:
        """VJWS-05: Comment too long returns 400 COMMENT_TOO_LONG."""
        payload = _feedback_payload(comment="x" * 257)
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "COMMENT_TOO_LONG"

    async def test_vjws_06_duplicate_feedback(self, client: AsyncClient) -> None:
        """VJWS-06: Duplicate feedback via JWS returns 409 FEEDBACK_EXISTS."""
        payload = _feedback_payload()
        _inject_mock_ok(payload)
        resp1 = await client.post("/feedback", json=_token_body(payload))
        assert resp1.status_code == 201

        resp2 = await client.post("/feedback", json=_token_body(payload))
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "FEEDBACK_EXISTS"

    async def test_vjws_07_mutual_reveal(self, client: AsyncClient) -> None:
        """VJWS-07: Mutual reveal works through JWS submission."""
        # Step 1: Alice -> Bob (sealed)
        payload_ab = _feedback_payload()
        _inject_mock_ok(payload_ab, agent_id=ALICE_ID)
        resp1 = await client.post("/feedback", json=_token_body(payload_ab))
        assert resp1.status_code == 201
        assert resp1.json()["visible"] is False

        # Step 2: Bob -> Alice (reveals both)
        payload_ba = _feedback_payload(
            from_agent_id=BOB_ID,
            to_agent_id=ALICE_ID,
            task_id="t-task-1",
        )
        _inject_mock_ok(payload_ba, agent_id=BOB_ID)
        resp2 = await client.post("/feedback", json=_token_body(payload_ba, kid=BOB_ID))
        assert resp2.status_code == 201
        assert resp2.json()["visible"] is True

        # Step 3: GET /feedback/task returns 2 visible entries
        resp3 = await client.get("/feedback/task/t-task-1")
        assert resp3.status_code == 200
        feedback_list = resp3.json()["feedback"]
        assert len(feedback_list) == 2
        for fb in feedback_list:
            assert fb["visible"] is True

    async def test_vjws_08_extra_fields_ignored(self, client: AsyncClient) -> None:
        """VJWS-08: Extra fields in JWS payload are ignored."""
        payload = _feedback_payload(
            feedback_id="fb-injected",
            submitted_at="2000-01-01T00:00:00Z",
            visible=True,
            is_admin=True,
        )
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 201
        data = response.json()
        # Service-generated values, not injected
        assert data["feedback_id"] != "fb-injected"
        assert data["feedback_id"].startswith("fb-")
        assert data["submitted_at"] != "2000-01-01T00:00:00Z"

    async def test_vjws_09_concurrent_duplicate_race(self, client: AsyncClient) -> None:
        """VJWS-09: Concurrent duplicate feedback race via JWS is safe."""
        payload = _feedback_payload(task_id="t-race-task")
        _inject_mock_ok(payload)

        async def _submit() -> int:
            resp = await client.post("/feedback", json=_token_body(payload))
            return resp.status_code

        results = await asyncio.gather(_submit(), _submit())
        status_codes = sorted(results)
        assert status_codes == [201, 409]


# =============================================================================
# Category 8: Cross-Cutting Security (SEC-AUTH-01 to SEC-AUTH-03)
# =============================================================================


@pytest.mark.unit
class TestCrossCuttingSecurity:
    """Category 8: Cross-Cutting Security Assertions."""

    async def test_sec_auth_01_error_envelope_consistency(self, client: AsyncClient) -> None:
        """SEC-AUTH-01: All auth errors have standard error envelope."""
        error_triggers = [
            # INVALID_JWS
            ({"token": ""}, None, None),
            # INVALID_PAYLOAD (wrong action)
            (None, "wrong_action", None),
            # FORBIDDEN (signer mismatch)
            (None, None, "mismatch"),
            # IDENTITY_SERVICE_UNAVAILABLE
            (None, None, "unavailable"),
        ]

        for trigger_type, action_val, special in error_triggers:
            if trigger_type is not None:
                response = await client.post("/feedback", json=trigger_type)
            elif action_val is not None:
                payload = _feedback_payload(action=action_val)
                _inject_mock_ok(payload)
                response = await client.post("/feedback", json=_token_body(payload))
            elif special == "mismatch":
                payload = _feedback_payload(from_agent_id=CAROL_ID)
                state = get_app_state()
                state.identity_client = make_mock_identity_client(
                    verify_response=_mock_verify_ok(ALICE_ID, payload),
                )
                response = await client.post("/feedback", json=_token_body(payload, kid=ALICE_ID))
            else:
                _inject_mock_error(
                    ServiceError(
                        "IDENTITY_SERVICE_UNAVAILABLE",
                        "Cannot reach Identity service",
                        502,
                        {},
                    )
                )
                token = make_jws_token(_feedback_payload())
                response = await client.post("/feedback", json={"token": token})

            data = response.json()
            assert "error" in data, f"Missing 'error' in response: {data}"
            assert isinstance(data["error"], str)
            assert "message" in data, f"Missing 'message' in response: {data}"
            assert isinstance(data["message"], str)
            assert "details" in data, f"Missing 'details' in response: {data}"
            assert isinstance(data["details"], dict)

    async def test_sec_auth_02_no_internal_leakage(self, client: AsyncClient) -> None:
        """SEC-AUTH-02: No internal error leakage in auth failures."""
        sensitive_patterns = [
            "traceback",
            "Traceback",
            "localhost:8001",
            "http://",
            "private_key",
            "secret",
            "stack",
            "File ",
        ]

        # Trigger INVALID_JWS
        resp1 = await client.post("/feedback", json={"token": ""})
        msg1 = resp1.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg1

        # Trigger FORBIDDEN
        _inject_mock_invalid_sig()
        token = make_jws_token(_feedback_payload())
        resp2 = await client.post("/feedback", json={"token": token})
        msg2 = resp2.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg2

        # Trigger IDENTITY_SERVICE_UNAVAILABLE
        _inject_mock_error(
            ServiceError("IDENTITY_SERVICE_UNAVAILABLE", "Cannot reach Identity service", 502, {})
        )
        resp3 = await client.post("/feedback", json={"token": make_jws_token(_feedback_payload())})
        msg3 = resp3.json()["message"]
        for pattern in sensitive_patterns:
            assert pattern not in msg3

    async def test_sec_auth_03_token_reuse_across_actions(self, client: AsyncClient) -> None:
        """SEC-AUTH-03: JWS token for another action is rejected."""
        # Token with escrow_lock action
        payload = _feedback_payload(action="escrow_lock")
        _inject_mock_ok(payload)
        response = await client.post("/feedback", json=_token_body(payload))
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"
