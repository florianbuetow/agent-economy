"""Worker rebuttal mediation endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_dispute
from tests.unit.routers.test_disputes import fixture_platform, stored_task

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str,
    rebuttal: str = "The deliverable meets the task specification.",
) -> Response:
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
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        dispute_id = stored_task(task_id)["dispute_id"]

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id=dispute_id
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rebuttal_pending"
        assert stored_task(task_id)["rebuttal_submitted_at"] is not None
        fixture_platform().submit_rebuttal.assert_awaited_once_with(
            dispute_id,
            "The deliverable meets the task specification.",
        )

    async def test_non_worker_rebuttal_is_rejected(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        carol_keypair: tuple[Ed25519PrivateKey, str],
        carol_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        dispute_id = stored_task(task_id)["dispute_id"]
        fixture_platform().submit_rebuttal = AsyncMock()

        response = await _submit_rebuttal(
            client, carol_keypair, carol_agent_id, task_id, dispute_id=dispute_id
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
        fixture_platform().submit_rebuttal.assert_not_awaited()
