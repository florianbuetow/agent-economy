"""Rebuttal-to-dispute binding (GAP-B9).

A worker may only rebut the dispute that the Court opened for their own task.
Task Board is the authorization layer, so it must bind the rebuttal's dispute_id
to the dispute_id it persisted when the task was disputed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    file_dispute,
    setup_task_in_dispute,
    setup_task_in_review,
)

if TYPE_CHECKING:
    from httpx import AsyncClient

REBUTTAL_TEXT = "The deliverable meets the task specification."


def _court_claim(dispute_id: str) -> dict[str, str]:
    return {"dispute_id": dispute_id, "status": "rebuttal_pending"}


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair,
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str,
    rebuttal: str = REBUTTAL_TEXT,
):
    """POST a signed rebuttal naming an explicit dispute_id."""
    payload = {
        "action": "submit_rebuttal",
        "task_id": task_id,
        "dispute_id": dispute_id,
        "worker_id": worker_id,
        "rebuttal": rebuttal,
    }
    token = make_jws_token(worker_keypair[0], worker_id, payload)
    return await client.post(f"/tasks/{task_id}/rebuttal", json={"token": token})


@pytest.mark.unit
class TestRebuttalDisputeBinding:
    async def test_worker_cannot_inject_rebuttal_into_another_tasks_dispute(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ) -> None:
        """GAP-B9 regression: Bob rebuts his own task while naming Carol's dispute_id."""
        state = get_app_state()
        state.platform_agent.file_claim = AsyncMock(
            side_effect=[_court_claim("disp-A"), _court_claim("disp-B")]
        )

        task_a = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        # Task B belongs to Carol; the Court opened dispute "disp-B" for it.
        await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, carol_keypair, carol_agent_id
        )

        state.platform_agent.submit_rebuttal = AsyncMock(return_value=_court_claim("disp-B"))

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_a, dispute_id="disp-B"
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        state.platform_agent.submit_rebuttal.assert_not_awaited()

    async def test_task_with_no_recorded_dispute_is_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """A disputed task with no stored dispute_id cannot be rebutted."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.store.update_task(task_id, {"dispute_id": None}, expected_status=None)
        state.platform_agent.submit_rebuttal = AsyncMock(return_value=_court_claim("disp-1"))

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id="disp-1"
        )

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
        state.platform_agent.submit_rebuttal.assert_not_awaited()

    async def test_matching_dispute_id_is_forwarded_once(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """A matching dispute_id forwards the server-stored id to the platform agent once."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.submit_rebuttal = AsyncMock(
            return_value={"dispute_id": "disp-1", "status": "rebuttal_pending"}
        )

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id="disp-1"
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rebuttal_pending"
        state.platform_agent.submit_rebuttal.assert_awaited_once_with("disp-1", REBUTTAL_TEXT)

    async def test_dispute_task_persists_court_dispute_id(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """dispute_task stores the Court-issued dispute_id on the task."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.file_claim = AsyncMock(return_value=_court_claim("disp-xyz"))

        response = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Spec not met."
        )

        assert response.status_code == 200
        assert response.json()["dispute_id"] == "disp-xyz"
        assert state.store.get_task(task_id)["dispute_id"] == "disp-xyz"

    async def test_court_without_dispute_id_leaves_task_undisputed(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ) -> None:
        """If Court returns no dispute_id, the task must not be marked disputed."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.file_claim = AsyncMock(return_value={"status": "rebuttal_pending"})

        response = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Spec not met."
        )

        assert response.status_code == 502
        assert response.json()["error"] == "court_unavailable"

        task = state.store.get_task(task_id)
        assert task["status"] == "submitted"
        assert task["dispute_id"] is None
