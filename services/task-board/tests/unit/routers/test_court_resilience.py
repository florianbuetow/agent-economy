"""Court-call resilience (GAP-E7).

A dead/unreachable Court must not surface as a raw 500 internal_error, and
must never leave a task in a half-transitioned state. dispute_task and
submit_rebuttal both call out to Court via the platform agent (file_claim /
submit_rebuttal); connect errors, timeouts, and bad HTTP status all map to a
typed 502 court_unavailable. Business status remains unchanged; a dispute
claim retains its native pending recovery owner.

Success-shaped responses without persisted claim evidence remain pending (202),
as covered by test_rebuttal_dispute_binding. Decode exceptions are tested here
separately from those acknowledgments.
"""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    new_operation_fields,
    setup_task_in_dispute,
    setup_task_in_review,
)
from tests.unit.routers.test_disputes import fixture_bank, fixture_platform, stored_task

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response


async def _file_dispute(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
    reason: str = "Spec not met.",
) -> Response:
    payload = {
        "action": "dispute_task",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "poster_id": poster_id,
        "reason": reason,
    }
    token = make_jws_token(poster_keypair[0], poster_id, payload)
    return await client.post(f"/tasks/{task_id}/dispute", json={"token": token})


async def _submit_rebuttal(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
    *,
    dispute_id: str,
    rebuttal: str = "The deliverable meets the task specification.",
) -> Response:
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
            JSONDecodeError("Malformed Court acknowledgment", "broken", 0),
            httpx.HTTPStatusError(
                "Server error",
                request=httpx.Request("POST", "http://court/disputes/file"),
                response=httpx.Response(500, request=httpx.Request("POST", "http://x")),
            ),
        ],
        ids=["connect_error", "timeout", "ack_decode_error", "http_status_error"],
    )
    async def test_dead_court_returns_502_and_leaves_task_submitted(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        court_error: Exception,
    ) -> None:
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        fixture_platform().file_claim = AsyncMock(side_effect=court_error)

        response = await _file_dispute(client, alice_keypair, alice_agent_id, task_id)

        assert response.status_code == 502
        assert response.json()["error"] == "court_unavailable"

        task = stored_task(task_id)
        assert task["status"] == "submitted"
        assert task["dispute_id"] is None
        assert task["disputed_at"] is None
        assert task["pending_transition"]["command"] == "dispute_task"
        fixture_platform().file_claim.assert_awaited_once()
        fixture_bank().escrow_release.assert_not_awaited()
        fixture_bank().escrow_split.assert_not_awaited()


@pytest.mark.unit
class TestRebuttalCourtResilience:
    """submit_rebuttal calls platform_agent.submit_rebuttal; a dead Court must not 500."""

    @pytest.mark.parametrize(
        "court_error",
        [
            httpx.ConnectError("Connection refused"),
            httpx.TimeoutException("Request timed out"),
            JSONDecodeError("Malformed Court acknowledgment", "broken", 0),
            httpx.HTTPStatusError(
                "Server error",
                request=httpx.Request("POST", "http://court/disputes/x/rebuttal"),
                response=httpx.Response(500, request=httpx.Request("POST", "http://x")),
            ),
        ],
        ids=["connect_error", "timeout", "ack_decode_error", "http_status_error"],
    )
    async def test_dead_court_returns_502_and_leaves_dispute_unchanged(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        court_error: Exception,
    ) -> None:
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        original = stored_task(task_id)
        dispute_id = original["dispute_id"]
        fixture_platform().submit_rebuttal = AsyncMock(side_effect=court_error)

        response = await _submit_rebuttal(
            client, bob_keypair, bob_agent_id, task_id, dispute_id=dispute_id
        )

        assert response.status_code == 502
        assert response.json()["error"] == "court_unavailable"

        task = stored_task(task_id)
        assert task["status"] == "disputed"
        assert task["dispute_id"] == dispute_id
        assert task == original
        fixture_platform().submit_rebuttal.assert_awaited_once()
        assert task["rebuttal_submitted_at"] is None
