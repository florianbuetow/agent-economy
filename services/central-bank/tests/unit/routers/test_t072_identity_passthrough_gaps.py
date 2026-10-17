"""T-072 coverage-sweep gap fixes: agent-tier operations (create_account,
get_balance) whose malformed-token detection is delegated entirely to Identity's
real `POST /agents/verify-jws` (verified in
services/identity/src/identity_service/services/agent_registry.py::AgentRegistry.verify_jws,
which raises ServiceError("invalid_jws", ..., 400, {}) for any token that is not a
three-part compact JWS — including an empty string). The mocked `identity_client`
in these tests replicates that exact production contract rather than asserting a
central-bank-local check that does not exist for this tier (see
central_bank_service/routers/helpers.py::verify_jws_token, which simply forwards
the raw token to Identity with no local shape validation, unlike the platform tier's
`decode_unverified_payload`).

Also covers BEARER-11, which needs no such mock: the ownership check precedes the
action-payload check on GET /accounts/{id}, and is testable directly against the
standard mocked `client` fixture.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state

from .conftest import make_jws_token


def _mock_identity_rejects_malformed_jws(state) -> None:  # type: ignore[no-untyped-def]
    """Replicate Identity's real verify_jws behavior for a malformed/empty token."""

    async def _verify_jws(_token: str) -> dict[str, object]:
        raise ServiceError("invalid_jws", "Token is not a valid JWS compact serialization", 400, {})

    state.identity_client.verify_jws = AsyncMock(side_effect=_verify_jws)


@pytest.mark.unit
class TestAgentTierMalformedTokenPassthrough:
    """AUTH-07, AUTH-08, BEARER-05."""

    async def test_empty_string_token_on_post_returns_invalid_jws(self, client):
        """AUTH-07: an empty string `token` on POST /accounts returns 400 invalid_jws,
        as Identity's own three-part-format check would reject it."""
        state = get_app_state()
        _mock_identity_rejects_malformed_jws(state)
        response = await client.post("/accounts", json={"token": ""})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"

    @pytest.mark.parametrize(
        "bad_token",
        ["not-a-jws-at-all", "only.two-parts", "four.parts.is.wrong.here"],
    )
    async def test_malformed_jws_shapes_on_post_return_invalid_jws(
        self, client, bad_token: str
    ) -> None:
        """AUTH-08: non-three-part token strings on POST /accounts return 400
        invalid_jws, as Identity's own compact-serialization check would reject
        them."""
        state = get_app_state()
        _mock_identity_rejects_malformed_jws(state)
        response = await client.post("/accounts", json={"token": bad_token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"

    async def test_empty_bearer_token_returns_invalid_jws(self, client):
        """BEARER-05: `Authorization: Bearer ` with nothing after it passes the local
        prefix check but forwards an empty token to Identity, which rejects it."""
        state = get_app_state()
        _mock_identity_rejects_malformed_jws(state)
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": "Bearer "},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
class TestOwnershipPrecedesPayloadValidation:
    """BEARER-11."""

    async def test_wrong_owner_reported_even_with_malformed_payload(self, client, agent_keypair):
        """BEARER-11: strict receiver context rejects the wrong action before ownership."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-alice",
            {"action": "not_a_real_action"},
            resource="GET /accounts/a-bob",
        )
        response = await client.get(
            "/accounts/a-bob",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
