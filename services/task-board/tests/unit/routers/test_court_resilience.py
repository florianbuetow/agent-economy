"""Court-call resilience (GAP-E7).

A dead/unreachable Court must not surface as a raw 500 internal_error, and
must never leave a task in a half-transitioned state. dispute_task and
submit_rebuttal both call out to Court via the platform agent (file_claim /
submit_rebuttal); connect errors, timeouts, and bad HTTP status all map to a
typed 502 court_unavailable, and the task's state is left exactly as it was.

The "Court returned no dispute_id" 502 case is already covered by
test_rebuttal_dispute_binding.py::test_court_without_dispute_id_leaves_task_undisputed
(hotfix H-2) — these tests cover the previously-uncovered transport-failure paths.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import setup_task_in_dispute, setup_task_in_review

if TYPE_CHECKING:
    from httpx import AsyncClient


async def _file_dispute(
    client: AsyncClient,
    poster_keypair,
    poster_id: str,
    task_id: str,
    reason: str = "Spec not met.",
):
    payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": poster_id,
        "reason": reason,
    }
    token = make_jws_token(poster_keypair[0], poster_id, payload)
    return await client.post(f"/tasks/{task_id}/dispute", json={"token": token})


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair,
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str,
    rebuttal: str = "The deliverable meets the task specification.",
):
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
class TestDisputeFilingCourtResilience:
    """dispute_task calls platform_agent.file_claim; a dead Court must not 500."""

    @pytest.mark.parametrize(
        "court_error",
        [
            httpx.ConnectError("Connection refused"),
            httpx.TimeoutException("Request timed out"),
            httpx.HTTPStatusError(
                "Server error",
                request=httpx.Request("POST", "http://court/disputes/file"),
                response=httpx.Response(500, request=httpx.Request("POST", "http://x")),
            ),
        ],
        ids=["connect_error", "timeout", "http_status_error"],
    )
    async def test_dead_court_returns_502_and_leaves_task_submitted(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        court_error: Exception,
    ) -> None:
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state = get_app_state()
        state.platform_agent.file_claim = AsyncMock(side_effect=court_error)

        response = await _file_dispute(client, alice_keypair, alice_agent_id, task_id)

        assert response.status_code == 502
        assert response.json()["error"] == "court_unavailable"

        task = state.store.get_task(task_id)
        assert task["status"] == "submitted"
        assert task["dispute_id"] is None
        assert task["disputed_at"] is None


@pytest.mark.unit
class TestRebuttalCourtResilience:
    """submit_rebuttal calls platform_agent.submit_rebuttal; a dead Court must not 500."""

    @pytest.mark.parametrize(
        "court_error",
        [
            httpx.ConnectError("Connection refused"),
            httpx.TimeoutException("Request timed out"),
            httpx.HTTPStatusError(
                "Server error",
                request=httpx.Request("POST", "http://court/disputes/x/rebuttal"),
                response=httpx.Response(500, request=httpx.Request("POST", "http://x")),
            ),
        ],
        ids=["connect_error", "timeout", "http_status_error"],
    )
    async def test_dead_court_returns_502_and_leaves_dispute_unchanged(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        court_error: Exception,
    ) -> None:
        state = get_app_state()
        state.platform_agent.file_claim = AsyncMock(
            return_value={"dispute_id": "disp-1", "status": "rebuttal_pending"}
        )
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        state.platform_agent.submit_rebuttal = AsyncMock(side_effect=court_error)

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id="disp-1"
        )

        assert response.status_code == 502
        assert response.json()["error"] == "court_unavailable"

        task = state.store.get_task(task_id)
        assert task["status"] == "disputed"
        assert task["dispute_id"] == "disp-1"
        assert task["rebuttal_submitted_at"] is None
