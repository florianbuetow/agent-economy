"""Tests for deliverable submission and approval endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

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


class TestDeliverableSubmission:
    """Tests for POST /tasks/{task_id}/submit endpoint."""

    @pytest.mark.unit
    async def test_sub_01_worker_submits_deliverable(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-01: Worker submits deliverable -- 200, status=submitted, submitted_at set."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "submitted"
        assert "submitted_at" in data
        # Verify submitted_at is a valid ISO 8601 timestamp
        submitted_at = datetime.fromisoformat(data["submitted_at"])
        assert submitted_at.tzinfo is not None or isinstance(submitted_at, datetime)

    @pytest.mark.unit
    async def test_sub_02_non_worker_cannot_submit(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """SUB-02: Non-worker cannot submit -- 403 FORBIDDEN."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        resp = await submit_deliverable(client, carol_keypair, carol_agent_id, task_id)
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_sub_03_wrong_status_cannot_submit(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-03: Cannot submit from non-execution status -- 409 INVALID_STATUS."""
        # Task is in OPEN status (no bid accepted)
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_sub_04_no_assets_uploaded(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-04: Cannot submit without assets uploaded -- 400 NO_ASSETS."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        # No assets uploaded
        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 400
        assert resp.json()["error"] == "NO_ASSETS"

    @pytest.mark.unit
    async def test_sub_05_sets_review_deadline(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-05: Submission sets review_deadline relative to submitted_at."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 200

        data = resp.json()
        assert "review_deadline" in data
        submitted_at = datetime.fromisoformat(data["submitted_at"])
        review_deadline = datetime.fromisoformat(data["review_deadline"])

        # review_deadline should be approximately submitted_at + review_deadline_seconds
        # Default review_deadline_seconds is 86400 (from config)
        expected_delta = timedelta(seconds=86400)
        actual_delta = review_deadline - submitted_at
        # Allow 5 seconds tolerance
        assert abs(actual_delta.total_seconds() - expected_delta.total_seconds()) < 5

    @pytest.mark.unit
    async def test_sub_06_already_submitted_cannot_submit_again(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-06: Already submitted -- 409 INVALID_STATUS (double submit)."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Submit again
        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_sub_07_wrong_action(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-07: Wrong action in submit token -- 400 INVALID_PAYLOAD."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        # Use wrong action
        private_key = bob_keypair[0]
        payload = {
            "action": "upload_asset",
            "task_id": task_id,
            "worker_id": bob_agent_id,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/submit", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_sub_08_after_execution_deadline_expired(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-08: Submit after execution deadline expired -- 409 INVALID_STATUS."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        # Fast-forward past the execution deadline (default 86400 seconds)
        future_time = datetime.now(tz=UTC) + timedelta(seconds=86400 + 3600)
        with freeze_time(future_time):
            resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)

        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_sub_09_missing_payload_fields(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """SUB-09: Missing payload fields -- 400 INVALID_PAYLOAD."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        # Submit with missing worker_id field
        private_key = bob_keypair[0]
        payload = {
            "action": "submit_deliverable",
            "task_id": task_id,
            # missing worker_id
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/submit", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"


class TestApproval:
    """Tests for POST /tasks/{task_id}/approve endpoint."""

    @pytest.mark.unit
    async def test_app_01_poster_approves_deliverable(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-01: Poster approves -- 200, status=approved, approved_at set."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 200

        data = resp.json()
        assert data["status"] == "approved"
        assert "approved_at" in data
        # Verify approved_at is a valid ISO 8601 timestamp
        approved_at = datetime.fromisoformat(data["approved_at"])
        assert approved_at.tzinfo is not None or isinstance(approved_at, datetime)

    @pytest.mark.unit
    async def test_app_02_non_poster_cannot_approve(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """APP-02: Non-poster cannot approve -- 403 FORBIDDEN."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await approve_task(client, carol_keypair, carol_agent_id, task_id)
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_app_03_wrong_status_cannot_approve(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
    ):
        """APP-03: Cannot approve from non-review status -- 409 INVALID_STATUS."""
        # Task is in OPEN status
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)

        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_app_04_approve_releases_escrow_to_worker(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-04: Approve releases escrow to worker (Central Bank mock confirms release)."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 200

        # Verify escrow_release was called on the Central Bank mock
        state = get_app_state()
        state.central_bank_client.escrow_release.assert_called()

    @pytest.mark.unit
    async def test_app_05_sets_approved_at(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-05: Approval sets approved_at timestamp."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        before = datetime.now(tz=UTC)
        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        after = datetime.now(tz=UTC)
        assert resp.status_code == 200

        data = resp.json()
        approved_at = datetime.fromisoformat(data["approved_at"])
        # Make timezone-aware comparison
        if approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=UTC)
        assert before <= approved_at <= after

    @pytest.mark.unit
    async def test_app_06_wrong_action(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-06: Wrong action in approve token -- 400 INVALID_PAYLOAD."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Use wrong action
        private_key = alice_keypair[0]
        payload = {
            "action": "dispute_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_app_07_signer_not_poster(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-07: Signer is not the poster -- 403 FORBIDDEN."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Bob (the worker) tries to approve claiming to be poster
        resp = await approve_task(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_app_08_review_deadline_auto_approval(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-08: Review deadline auto-approval -- after deadline, GET shows completed/approved."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Fast-forward past the review deadline (default 86400 seconds)
        future_time = datetime.now(tz=UTC) + timedelta(seconds=86400 + 3600)
        with freeze_time(future_time):
            resp = await client.get(f"/tasks/{task_id}")

        assert resp.status_code == 200
        data = resp.json()
        # After review deadline, task should be auto-approved
        assert data["status"] in ("approved", "completed")

    @pytest.mark.unit
    async def test_app_09_missing_payload_fields(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """APP-09: Missing payload fields -- 400 INVALID_PAYLOAD."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Submit with missing poster_id field
        private_key = alice_keypair[0]
        payload = {
            "action": "approve_task",
            "task_id": task_id,
            # missing poster_id
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"
