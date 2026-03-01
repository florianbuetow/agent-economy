"""Dispute and ruling endpoint tests for Task Board service."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    create_task,
    file_dispute,
    make_task_id,
    setup_task_in_dispute,
    setup_task_in_review,
    submit_ruling,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestDispute:
    """Tests for POST /tasks/{task_id}/dispute endpoint (DIS-01 to DIS-10)."""

    @pytest.mark.unit
    async def test_poster_disputes_deliverable(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-01: Poster disputes deliverable - returns 200 with disputed status."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        reason = "The login page does not validate email format."
        resp = await file_dispute(client, alice_keypair, alice_agent_id, task_id, reason=reason)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "disputed"
        assert "disputed_at" in data
        assert data["dispute_reason"] == reason

    @pytest.mark.unit
    async def test_non_poster_cannot_dispute(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """DIS-02: Non-poster agent cannot dispute - returns 403 FORBIDDEN."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await file_dispute(
            client, carol_keypair, carol_agent_id, task_id, reason="Not my task"
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_worker_cannot_dispute(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-03: Worker cannot dispute their own task - returns 403 FORBIDDEN."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await file_dispute(client, bob_keypair, bob_agent_id, task_id, reason="Self dispute")

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_cannot_dispute_non_submitted_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
    ):
        """DIS-04: Cannot dispute a task not in SUBMITTED status - returns 409."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        resp = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason="Too early"
        )

        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_empty_dispute_reason(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-05: Empty dispute reason - returns 400 INVALID_REASON."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await file_dispute(client, alice_keypair, alice_agent_id, task_id, reason="")

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_REASON"

    @pytest.mark.unit
    async def test_dispute_reason_exceeding_max_length(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-06: Dispute reason exceeding max length - returns 400 INVALID_REASON."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        long_reason = "x" * 10_001
        resp = await file_dispute(
            client, alice_keypair, alice_agent_id, task_id, reason=long_reason
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_REASON"

    @pytest.mark.unit
    async def test_dispute_reason_at_max_length(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-07: Dispute reason at exactly max length (10,000 chars) - returns 200."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        max_reason = "x" * 10_000
        resp = await file_dispute(client, alice_keypair, alice_agent_id, task_id, reason=max_reason)

        assert resp.status_code == 200

    @pytest.mark.unit
    async def test_wrong_action_in_dispute_token(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-08: Wrong action in dispute token - returns 400 INVALID_PAYLOAD."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = alice_keypair[0]
        payload = {
            "action": "approve_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "reason": "Wrong action test",
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_dispute_on_nonexistent_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
    ):
        """DIS-09: Dispute on non-existent task - returns 404 TASK_NOT_FOUND."""
        fake_task_id = "t-00000000-0000-0000-0000-000000000000"
        resp = await file_dispute(
            client, alice_keypair, alice_agent_id, fake_task_id, reason="Ghost task"
        )

        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_task_id_in_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """DIS-10: task_id in payload must match URL path - returns 400."""
        task_id_1 = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        private_key = alice_keypair[0]
        payload = {
            "action": "dispute_task",
            "task_id": task_id_2,
            "poster_id": alice_agent_id,
            "reason": "Mismatch test",
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id_1}/dispute", json={"token": token})

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"


class TestRuling:
    """Tests for POST /tasks/{task_id}/ruling endpoint (RUL-01 to RUL-13)."""

    @pytest.mark.unit
    async def test_platform_records_ruling(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-01: Platform submits ruling - returns 200 with ruled status."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        ruling_id = f"rul-{uuid.uuid4()}"
        private_key = platform_keypair[0]
        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": ruling_id,
            "worker_pct": 40,
            "ruling_summary": "Worker delivered but omitted email validation.",
        }
        token = make_jws_token(private_key, platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ruled"
        assert "ruled_at" in data
        assert data["ruling_id"] == ruling_id
        assert data["worker_pct"] == 40
        assert data["ruling_summary"] == payload["ruling_summary"]

    @pytest.mark.unit
    async def test_non_platform_agent_cannot_record_ruling(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """RUL-02: Non-platform agent cannot record ruling - returns 403 FORBIDDEN."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        ruling_id = f"rul-{uuid.uuid4()}"
        private_key = alice_keypair[0]
        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": ruling_id,
            "worker_pct": 50,
            "ruling_summary": "Alice tries to rule.",
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_cannot_rule_on_non_disputed_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-03: Cannot rule on non-DISPUTED task - returns 409 INVALID_STATUS."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=50,
            ruling_summary="Should fail - not disputed",
        )

        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_worker_pct_zero_full_poster_win(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-04: worker_pct=0 means full refund to poster - returns 200."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=0,
            ruling_summary="Full poster win",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_pct"] == 0

        state = get_app_state()
        state.central_bank_client.escrow_release.assert_called_once()

    @pytest.mark.unit
    async def test_worker_pct_100_full_worker_win(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-05: worker_pct=100 means full payout to worker - returns 200."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=100,
            ruling_summary="Full worker win",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_pct"] == 100

        state = get_app_state()
        state.central_bank_client.escrow_release.assert_called_once()

    @pytest.mark.unit
    async def test_worker_pct_50_split(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-06: worker_pct=50 means split payout - returns 200."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=50,
            ruling_summary="Split ruling",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_pct"] == 50

        state = get_app_state()
        state.central_bank_client.escrow_split.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "invalid_pct",
        [-1, 101, 3.5, "abc", None],
        ids=["negative", "over_100", "float", "string", "null"],
    )
    async def test_invalid_worker_pct_values(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
        invalid_pct,
    ):
        """RUL-07: Invalid worker_pct values - returns 400 INVALID_WORKER_PCT."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        ruling_id = f"rul-{uuid.uuid4()}"
        private_key = platform_keypair[0]
        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": ruling_id,
            "worker_pct": invalid_pct,
            "ruling_summary": "Invalid pct test",
        }
        token = make_jws_token(private_key, platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_WORKER_PCT"

    @pytest.mark.unit
    async def test_ruling_summary_stored_in_response(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-08: Ruling summary is stored and returned in response."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        summary = "Worker delivered partial work; email validation was omitted."
        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=60,
            ruling_summary=summary,
        )

        assert resp.status_code == 200
        assert resp.json()["ruling_summary"] == summary

    @pytest.mark.unit
    async def test_ruling_sets_ruled_at_worker_pct_summary(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-09: Ruling sets ruled_at, worker_pct, and ruling_summary fields."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        summary = "Judgment rendered."
        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=75,
            ruling_summary=summary,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "ruled_at" in data
        assert data["worker_pct"] == 75
        assert data["ruling_summary"] == summary

    @pytest.mark.unit
    async def test_wrong_action_in_ruling_token(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-10: Wrong action in ruling token - returns 400 INVALID_PAYLOAD."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        ruling_id = f"rul-{uuid.uuid4()}"
        private_key = platform_keypair[0]
        payload = {
            "action": "approve_task",
            "task_id": task_id,
            "ruling_id": ruling_id,
            "worker_pct": 50,
            "ruling_summary": "Wrong action",
        }
        token = make_jws_token(private_key, platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_ruling_on_nonexistent_task(
        self,
        client: AsyncClient,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-11: Ruling on non-existent task - returns 404 TASK_NOT_FOUND."""
        fake_task_id = "t-00000000-0000-0000-0000-000000000000"
        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            fake_task_id,
            worker_pct=50,
            ruling_summary="Ghost task ruling",
        )

        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_missing_payload_fields_in_ruling(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-12: Missing required payload fields - returns 400 INVALID_PAYLOAD."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = platform_keypair[0]

        # Missing ruling_id
        payload_no_ruling_id = {
            "action": "record_ruling",
            "task_id": task_id,
            "worker_pct": 50,
            "ruling_summary": "Missing ruling_id",
        }
        token = make_jws_token(private_key, platform_agent_id, payload_no_ruling_id)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

        # Missing worker_pct
        payload_no_pct = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": f"rul-{uuid.uuid4()}",
            "ruling_summary": "Missing worker_pct",
        }
        token = make_jws_token(private_key, platform_agent_id, payload_no_pct)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

        # Missing ruling_summary
        payload_no_summary = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": f"rul-{uuid.uuid4()}",
            "worker_pct": 50,
        }
        token = make_jws_token(private_key, platform_agent_id, payload_no_summary)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_central_bank_unavailable_during_ruling(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        platform_keypair,
        platform_agent_id,
    ):
        """RUL-13: Central Bank unavailable during escrow - returns 502."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Make central bank unavailable for escrow operations
        state = get_app_state()
        state.central_bank_client.escrow_release = AsyncMock(
            side_effect=ConnectionError("Central Bank unreachable")
        )
        state.central_bank_client.escrow_split = AsyncMock(
            side_effect=ConnectionError("Central Bank unreachable")
        )

        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=50,
            ruling_summary="Bank is down",
        )

        assert resp.status_code == 502
