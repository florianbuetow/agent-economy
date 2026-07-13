"""WP-06.3 (GAP-A8/T-039): rebuttal-window enforcement on /rule.

Ruling is allowed only when a rebuttal exists on the dispute or the rebuttal
deadline has passed; otherwise Court rejects the ruling trigger with
``409 dispute_not_ready``. The window-closed cases (no rebuttal, expired window)
stay covered by the existing RULE-14/RULE-19/LIFE-02/LIFE-04 acceptance tests
(frozen-test exception #6) -- this file covers the still-open-window case those
tests do not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.unit.routers.conftest import (
    PLATFORM_AGENT_ID,
    file_dispute,
    inject_identity_verify,
    inject_judge,
    ruling_payload,
    token_body,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.unit
async def test_rule_without_rebuttal_before_deadline_returns_dispute_not_ready(
    client: AsyncClient,
) -> None:
    """T-039: no rebuttal + unexpired rebuttal window -> 409 dispute_not_ready."""
    dispute = await file_dispute(client)
    dispute_id = dispute["dispute_id"]
    inject_judge(worker_pct=80)

    rule_pay = ruling_payload(dispute_id)
    inject_identity_verify(PLATFORM_AGENT_ID, rule_pay)
    response = await client.post(f"/disputes/{dispute_id}/rule", json=token_body(rule_pay))

    assert response.status_code == 409
    assert response.json()["error"] == "dispute_not_ready"

    # The dispute must still be rebuttal_pending -- the rejected attempt is not a
    # side-effecting failure.
    get_response = await client.get(f"/disputes/{dispute_id}")
    assert get_response.json()["status"] == "rebuttal_pending"
