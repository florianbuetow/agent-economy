"""WP-06 GAP-B4: Court asserts JWS header kid == platform agent id.

A token whose signature verifies as the platform key but whose protected-header
``kid`` is not the platform agent id must be rejected. Crypto authenticity alone
is not sufficient — the spec'd identity binding (kid) must also match.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from court_service.core.state import get_app_state
from tests.helpers import make_mock_platform_agent
from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    file_and_rebut,
    file_dispute,
    file_dispute_payload,
    inject_identity_verify,
    rebuttal_payload,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

WRONG_KID = "a-not-the-platform-id"


@pytest.mark.unit
class TestPlatformKidAssertion:
    """GAP-B4: kid must equal the platform agent id on platform-signed writes."""

    async def test_file_wrong_kid_rejected(self, client: AsyncClient) -> None:
        """File dispute: valid signature but wrong header kid -> 403 forbidden."""
        payload = file_dispute_payload()
        # The default local verifier accepts the fixture signature but binds kid.
        response = await client.post("/disputes/file", json=token_body(payload, kid=WRONG_KID))
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_rebuttal_wrong_kid_rejected(self, client: AsyncClient) -> None:
        """Submit rebuttal: valid signature but wrong header kid -> 403 forbidden."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        get_app_state().platform_agent = make_mock_platform_agent(agent_id=PLATFORM_AGENT_ID)
        reb_pay = rebuttal_payload(dispute_id)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay, kid=WRONG_KID),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_rule_wrong_kid_rejected(self, client: AsyncClient) -> None:
        """Trigger ruling: valid signature but wrong header kid -> 403 forbidden."""
        dispute = await file_and_rebut(client)
        dispute_id = dispute["dispute_id"]
        get_app_state().platform_agent = make_mock_platform_agent(agent_id=PLATFORM_AGENT_ID)
        rule_pay = ruling_payload(dispute_id)
        response = await client.post(
            f"/disputes/{dispute_id}/rule",
            json=token_body(rule_pay, kid=WRONG_KID),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_correct_kid_still_accepted(self, client: AsyncClient) -> None:
        """Sanity: the platform kid path still succeeds (mutation anchor)."""
        payload = file_dispute_payload()
        inject_identity_verify(PLATFORM_AGENT_ID, payload)
        response = await client.post(
            "/disputes/file", json=token_body(payload, kid=PLATFORM_AGENT_ID)
        )
        assert response.status_code == 201
