"""Workers can rebut only the actual Court claim bound to their own task."""

from __future__ import annotations

from functools import partial
from json import JSONDecodeError
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import file_dispute, setup_task_in_dispute, setup_task_in_review
from tests.unit.routers.test_disputes import fixture_bank, fixture_platform, stored_dispute_id

if TYPE_CHECKING:
    from httpx import AsyncClient

REBUTTAL_TEXT = "The deliverable meets the task specification."


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair: Any,
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str,
    rebuttal: str = REBUTTAL_TEXT,
) -> Any:
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
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
        carol_keypair: Any,
        carol_agent_id: str,
    ) -> None:
        task_a = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        task_b = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, carol_keypair, carol_agent_id
        )
        dispute_b = stored_dispute_id(task_b)
        before_a = app.state.gateway_store.get_task(task_a)
        before_b = app.state.gateway_store.get_task(task_b)
        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_a, dispute_id=dispute_b
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"
        fixture_platform().submit_rebuttal.assert_not_awaited()
        assert app.state.gateway_store.get_task(task_a) == before_a
        assert app.state.gateway_store.get_task(task_b) == before_b
        assert (
            app.state.gateway_store.writer.connection.execute(
                "SELECT COUNT(*) FROM court_rebuttals"
            ).fetchone()[0]
            == 0
        )

    async def test_task_with_no_recorded_dispute_is_rejected(
        self,
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        dispute_id = stored_dispute_id(task_id)
        store = app.state.gateway_store
        # Deliberate fixture corruption: normal writers cannot erase lifecycle ownership.
        store.writer.connection.execute(
            "UPDATE board_tasks SET dispute_id=NULL WHERE task_id=?", (task_id,)
        )
        store.writer.connection.commit()
        before = store.get_task(task_id)
        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id=dispute_id
        )
        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
        fixture_platform().submit_rebuttal.assert_not_awaited()
        assert store.get_task(task_id) == before

    async def test_matching_dispute_id_is_forwarded_once(
        self,
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        dispute_id = stored_dispute_id(task_id)
        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id=dispute_id
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rebuttal_pending"
        fixture_platform().submit_rebuttal.assert_awaited_once_with(dispute_id, REBUTTAL_TEXT)
        task = app.state.gateway_store.get_task(task_id)
        assert task["rebuttal_submitted_at"] is not None
        rows = app.state.gateway_store.writer.connection.execute(
            "SELECT claim_id,agent_id,content,submitted_at FROM court_rebuttals"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (dispute_id, bob_agent_id, REBUTTAL_TEXT, task["rebuttal_submitted_at"])
        ]

    async def test_dispute_task_persists_court_dispute_id(
        self,
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        fixture_platform().file_claim = AsyncMock(
            side_effect=partial(app.state.gateway_effects.persist_claim, claim_id="disp-xyz")
        )
        response = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Spec not met."
        )
        assert response.status_code == 200
        assert response.json()["dispute_id"] == "disp-xyz"
        assert app.state.gateway_store.get_task(task_id)["dispute_id"] == "disp-xyz"

    async def test_court_without_dispute_id_leaves_task_undisputed(
        self,
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        """An acknowledgment without committed evidence leaves a visible pending claim."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        fixture_platform().file_claim = AsyncMock(return_value={"status": "rebuttal_pending"})
        response = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Spec not met."
        )
        assert response.status_code == 202
        assert response.json()["operation"]["operation_state"] == "claimed"
        task = app.state.gateway_store.get_task(task_id)
        assert task["status"] == "submitted"
        assert task["dispute_id"] is None
        assert task["pending_transition"]["command"] == "dispute_task"
        assert (
            app.state.gateway_store.writer.connection.execute(
                "SELECT COUNT(*) FROM court_claims WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            == 0
        )
        assert (
            app.state.gateway_store.writer.connection.execute(
                "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='task.disputed'",
                (task_id,),
            ).fetchone()[0]
            == 0
        )
        fixture_bank().escrow_release.assert_not_awaited()
        fixture_bank().escrow_split.assert_not_awaited()

    async def test_persisted_claim_survives_malformed_acknowledgment(
        self,
        app: Any,
        client: AsyncClient,
        alice_keypair: Any,
        alice_agent_id: str,
        bob_keypair: Any,
        bob_agent_id: str,
    ) -> None:
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        async def _persist_then_fail(**kwargs: Any) -> dict[str, Any]:
            await app.state.gateway_effects.persist_claim(claim_id="disp-ack-lost", **kwargs)
            raise JSONDecodeError("Lost Court acknowledgment", "broken", 0)

        fixture_platform().file_claim = AsyncMock(side_effect=_persist_then_fail)
        response = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Spec not met."
        )
        assert response.status_code == 200
        assert response.json()["status"] == "disputed"
        assert response.json()["dispute_id"] == "disp-ack-lost"
        assert response.json()["operation"]["operation_state"] == "completed"
        fixture_platform().file_claim.assert_awaited_once()
