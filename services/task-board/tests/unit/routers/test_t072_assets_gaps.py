"""T-072 coverage-sweep gap fix for asset upload impersonation.

Closes AU-11 from ``docs/specifications/service-tests/task-board-service-tests.md``,
which the existing ``test_assets.py`` suite does not exercise: its own
"AU-11"-labeled test covers the poster-cannot-upload case (spec AU-03) instead.
This is a new test only -- no existing test file is modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_execution

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestAssetUploadGaps:
    """Gaps in Category 7: Asset Upload (POST /tasks/{task_id}/assets)."""

    @pytest.mark.unit
    async def test_au11_impersonation_signer_not_claimed_worker_id(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
        carol_keypair,
        carol_agent_id: str,
    ) -> None:
        """AU-11: Carol signs claiming worker_id=bob -- returns 403 forbidden.

        Distinct from AU-04 (an unrelated third party who is simply not the
        task's worker, checked after task lookup): here the payload itself
        claims a worker_id the signer does not hold, exercising the earlier
        signer-vs-payload check (step 9a) rather than the signer-vs-task
        check (step 9b).
        """
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        payload = {
            "action": "upload_asset",
            "task_id": task_id,
            "worker_id": bob_agent_id,
        }
        token = make_jws_token(carol_keypair[0], carol_agent_id, payload)
        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("test.txt", b"impersonation attempt", "application/octet-stream")},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"
