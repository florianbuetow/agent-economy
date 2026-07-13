"""WP-06 (H-2 court-side deferral): rebuttal party must match dispute respondent.

Task Board forwards the worker (respondent) id alongside a platform-signed
rebuttal. Court asserts that forwarded party equals the respondent recorded on
the dispute it opened, rejecting a rebuttal whose party is mismatched. A rebuttal
with no forwarded party stays accepted (legacy tolerance).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    RESPONDENT_ID,
    file_dispute,
    inject_identity_verify,
    rebuttal_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
class TestRebuttalPartyAssertion:
    """The forwarded rebuttal party must match the dispute respondent."""

    async def test_mismatched_respondent_rejected(self, client: AsyncClient) -> None:
        """A rebuttal naming a party other than the dispute respondent -> 403."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, respondent_id="a-different-worker")
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_matching_respondent_accepted(self, client: AsyncClient) -> None:
        """A rebuttal naming the correct dispute respondent -> 200."""
        dispute = await file_dispute(client)
        dispute_id = dispute["dispute_id"]
        reb_pay = rebuttal_payload(dispute_id, respondent_id=RESPONDENT_ID)
        inject_identity_verify(PLATFORM_AGENT_ID, reb_pay)
        response = await client.post(
            f"/disputes/{dispute_id}/rebuttal",
            json=token_body(reb_pay),
        )
        assert response.status_code == 200
        assert response.json()["rebuttal"] is not None
