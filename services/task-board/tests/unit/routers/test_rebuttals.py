"""Worker rebuttal mediation endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_dispute

if TYPE_CHECKING:
    from httpx import AsyncClient


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair,
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str = "disp-1",
    rebuttal: str = "The deliverable meets the task specification.",
):
    private_key = worker_keypair[0]
    payload = {
        "action": "submit_rebuttal",
        "task_id": task_id,
        "dispute_id": dispute_id,
        "worker_id": worker_id,
        "rebuttal": rebuttal,
    }
    token = make_jws_token(private_key, worker_id, payload)
    return await client.post(f"/tasks/{task_id}/rebuttal", json={"token": token})


@pytest.mark.unit
class TestWorkerRebuttal:
    async def test_worker_rebuttal_is_forwarded_by_platform(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.submit_rebuttal = AsyncMock(
            return_value={"dispute_id": "disp-1", "status": "rebuttal_pending"}
        )

        response = await _submit_rebuttal(client, bob_keypair, bob_agent_id, task_id)

        assert response.status_code == 200
        assert response.json()["status"] == "rebuttal_pending"
        state.platform_agent.submit_rebuttal.assert_awaited_once_with(
            "disp-1",
            "The deliverable meets the task specification.",
        )

    async def test_non_worker_rebuttal_is_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.submit_rebuttal = AsyncMock()

        response = await _submit_rebuttal(client, carol_keypair, carol_agent_id, task_id)

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
        state.platform_agent.submit_rebuttal.assert_not_awaited()
