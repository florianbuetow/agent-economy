"""T-072 coverage-sweep gap fixes for deliverable submission and approval.

Each test below closes a spec ID from ``docs/specifications/service-tests/
task-board-service-tests.md`` that the existing ``test_submission.py`` suite
does not actually exercise under its own drifted ``SUB-nn``/``APP-nn``
docstring labels. These are new tests only -- no existing test file is
modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    approve_task,
    create_task,
    make_task_id,
    setup_task_in_execution,
    setup_task_in_review,
    submit_deliverable,
    upload_asset,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestDeliverableSubmissionGaps:
    """Gaps in Category 9: Deliverable Submission (POST /tasks/{task_id}/submit)."""

    @pytest.mark.unit
    async def test_sub03_poster_cannot_submit(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """SUB-03: Poster cannot submit their own task -- returns 403 forbidden.

        Distinct from the generic non-worker case (an unrelated third party):
        here the poster specifically attempts to submit, exercising the
        signer-vs-task-worker check for the one role that has other special
        permissions (approve/dispute) on this task.
        """
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        resp = await submit_deliverable(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    @pytest.mark.unit
    async def test_sub08_submit_on_nonexistent_task(
        self,
        client: AsyncClient,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """SUB-08: Submit on non-existent task returns 404 task_not_found."""
        fake_task_id = "t-00000000-0000-0000-0000-000000000000"
        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, fake_task_id)
        assert resp.status_code == 404
        assert resp.json()["error"] == "task_not_found"

    @pytest.mark.unit
    async def test_sub09_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """SUB-09: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)
        other_task_id = make_task_id()

        submit_payload = {
            "action": "submit_deliverable",
            "task_id": other_task_id,
            "worker_id": bob_agent_id,
        }
        token = make_jws_token(bob_keypair[0], bob_agent_id, submit_payload)
        resp = await client.post(f"/tasks/{task_id}/submit", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"


class TestApprovalGaps:
    """Gaps in Category 10: Approval (POST /tasks/{task_id}/approve)."""

    @pytest.mark.unit
    async def test_app07_central_bank_unavailable_on_approve_release(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """APP-07: Central Bank unavailable during escrow release returns 502."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        state = get_app_state()
        state.central_bank_client.escrow_release = AsyncMock(
            side_effect=ConnectionError("Central Bank unreachable")
        )

        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 502
        assert resp.json()["error"] == "central_bank_unavailable"

    @pytest.mark.unit
    async def test_app08_approve_on_nonexistent_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """APP-08: Approve on non-existent task returns 404 task_not_found."""
        fake_task_id = "t-00000000-0000-0000-0000-000000000000"
        resp = await approve_task(client, alice_keypair, alice_agent_id, fake_task_id)
        assert resp.status_code == 404
        assert resp.json()["error"] == "task_not_found"

    @pytest.mark.unit
    async def test_app09_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """APP-09: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        other_task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=other_task_id)

        approve_payload = {
            "action": "approve_task",
            "task_id": other_task_id,
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(alice_keypair[0], alice_agent_id, approve_payload)
        resp = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"
