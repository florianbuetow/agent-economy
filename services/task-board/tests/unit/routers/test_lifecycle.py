"""Lifecycle and deadline tests for the Task Board service (LIFE-01 to LIFE-12)."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

from tests.helpers import make_jws_token

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

from tests.unit.routers.conftest import (
    accept_bid,
    approve_task,
    create_task,
    file_dispute,
    make_task_id,
    setup_task_in_review,
    submit_bid,
    submit_deliverable,
    submit_ruling,
    upload_asset,
)


# ---------------------------------------------------------------------------
# Helper: cancel a task (not in conftest, needed by several lifecycle tests)
# ---------------------------------------------------------------------------
async def cancel_task(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
) -> object:
    """Cancel a task via POST /tasks/{task_id}/cancel."""
    private_key = poster_keypair[0]
    payload = {
        "action": "cancel_task",
        "task_id": task_id,
        "poster_id": poster_id,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/cancel", json={"token": token})


class TestLifecycle:
    """LIFE-01 through LIFE-12: Task lifecycle and deadline tests."""

    # -----------------------------------------------------------------------
    # LIFE-01  Full happy path
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_full_happy_path(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-01: Full happy path create -> bid -> accept -> upload -> submit -> approve."""
        task_id = make_task_id()

        # 1. Alice creates a task
        resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert resp.status_code == 201
        assert resp.json()["status"] == "open"

        # 2. Bob bids
        resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 201
        bid_id = resp.json()["bid_id"]
        assert bid_id is not None

        # 3. Alice accepts Bob's bid
        resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "accepted"
        assert body["worker_id"] == bob_agent_id

        # 4. Bob uploads an asset
        resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 201
        assert resp.json()["asset_id"] is not None

        # 5. Bob submits deliverable
        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

        # 6. Alice approves
        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["approved_at"] is not None

    # -----------------------------------------------------------------------
    # LIFE-02  Dispute flow
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_dispute_flow(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        platform_agent_id: str,
    ) -> None:
        """LIFE-02: Dispute flow through create, bid, accept, submit, dispute, ruling."""
        task_id = make_task_id()

        # 1. Alice creates a task
        resp = await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        assert resp.status_code == 201
        assert resp.json()["status"] == "open"

        # 2. Bob bids and is accepted
        resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 201
        bid_id = resp.json()["bid_id"]

        resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # 3. Bob uploads and submits
        resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 201

        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

        # 4. Alice disputes
        resp = await file_dispute(
            client,
            alice_keypair,
            alice_agent_id,
            task_id,
            reason="Deliverable does not meet specification",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "disputed"
        assert body["dispute_reason"] is not None
        assert body["disputed_at"] is not None

        # 5. Platform records a ruling
        resp = await submit_ruling(
            client,
            platform_keypair,
            platform_agent_id,
            task_id,
            worker_pct=60,
            ruling_summary="Partial delivery accepted",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ruled"
        assert body["ruling_id"] is not None
        assert body["worker_pct"] == 60
        assert body["ruling_summary"] == "Partial delivery accepted"
        assert body["ruled_at"] is not None

    # -----------------------------------------------------------------------
    # LIFE-03  Bidding deadline expiry
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_bidding_deadline_expiry(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """LIFE-03: Bidding deadline expiry cancels/expires task via lazy evaluation."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with 1-second bidding deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=1,
            )
            assert resp.status_code == 201
            assert resp.json()["status"] == "open"

            # Advance past deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # GET should show expired
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "expired"
            assert body["expired_at"] is not None

    # -----------------------------------------------------------------------
    # LIFE-04  Execution deadline expiry
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_execution_deadline_expiry(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-04: Execution deadline expiry marks task as expired."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with short execution deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                execution_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Bob bids and is accepted
            resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 201
            bid_id = resp.json()["bid_id"]

            resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

            # Advance past execution deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # GET should show expired
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "expired"
            assert body["expired_at"] is not None

    # -----------------------------------------------------------------------
    # LIFE-05  Review deadline auto-approves
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_review_deadline_auto_approves(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-05: Review deadline expiry auto-approves task."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with short review deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                review_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Bob bids and is accepted
            resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 201
            bid_id = resp.json()["bid_id"]

            resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
            assert resp.status_code == 200

            # Bob uploads and submits
            resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 201

            resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 200
            assert resp.json()["status"] == "submitted"

            # Advance past review deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # GET should show approved (auto-approve)
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "approved"
            assert body["approved_at"] is not None

    # -----------------------------------------------------------------------
    # LIFE-06  Expired tasks in GET /tasks listing
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_expired_tasks_in_listing(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """LIFE-06: Expired tasks show correct status in GET /tasks listing."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with 1-second bidding deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Advance past deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # GET /tasks should show the task as expired
            resp = await client.get("/tasks")
            assert resp.status_code == 200
            tasks = resp.json()["tasks"]
            matching = [t for t in tasks if t["task_id"] == task_id]
            assert len(matching) == 1
            assert matching[0]["status"] == "expired"

    # -----------------------------------------------------------------------
    # LIFE-07  Cannot bid on lazily expired task
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_cannot_bid_on_expired_task(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-07: Cannot bid on expired (lazily cancelled) task."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with 1-second bidding deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Advance past deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # Bob tries to bid - should fail with 409 INVALID_STATUS
            resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 409
            assert resp.json()["error"] == "INVALID_STATUS"

    # -----------------------------------------------------------------------
    # LIFE-08  Cannot submit on expired execution deadline
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_cannot_submit_on_expired_execution(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-08: Cannot submit on expired execution deadline."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with short execution deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                execution_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Bob bids and is accepted
            resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 201
            bid_id = resp.json()["bid_id"]

            resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
            assert resp.status_code == 200
            assert resp.json()["status"] == "accepted"

            # Upload asset while still in time
            resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 201

            # Advance past execution deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # Bob tries to submit - should fail
            resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
            assert resp.status_code == 409
            assert resp.json()["error"] == "INVALID_STATUS"

    # -----------------------------------------------------------------------
    # LIFE-09  Terminal status permanence
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_terminal_status_blocks_all_mutations(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        platform_agent_id: str,
    ) -> None:
        """LIFE-09: Terminal states block all mutations."""
        # Create a task and approve it (terminal state: approved)
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        # All mutations on a terminal (approved) task should return 409
        # 1. Cancel
        resp = await cancel_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

        # 2. Bid
        resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

        # 3. Submit deliverable
        resp = await submit_deliverable(client, bob_keypair, bob_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

        # 4. Approve
        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

        # 5. Dispute
        resp = await file_dispute(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

        # 6. Ruling
        resp = await submit_ruling(client, platform_keypair, platform_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    # -----------------------------------------------------------------------
    # LIFE-10  Deadline evaluation does not affect terminal states
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_deadline_does_not_affect_terminal_states(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """LIFE-10: Lazy evaluation on GET does not overwrite terminal states."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create a task with a bidding deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=60,
            )
            assert resp.status_code == 201

            # Cancel the task (terminal state)
            resp = await cancel_task(client, alice_keypair, alice_agent_id, task_id)
            assert resp.status_code == 200
            assert resp.json()["status"] == "cancelled"

            # Advance past the bidding deadline
            frozen.tick(delta=datetime.timedelta(seconds=120))

            # GET should still show cancelled, NOT expired
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "cancelled"

    # -----------------------------------------------------------------------
    # LIFE-11  Terminal status is permanent across multiple GETs
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_terminal_status_permanent_across_gets(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """LIFE-11: Terminal status is permanent -- multiple GETs show same terminal status."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            # Create task with short bidding deadline
            resp = await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=1,
            )
            assert resp.status_code == 201

            # Advance past deadline
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # First GET - should show expired
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "expired"
            first_expired_at = resp.json()["expired_at"]
            assert first_expired_at is not None

            # Advance time further
            frozen.tick(delta=datetime.timedelta(seconds=60))

            # Second GET - should still show expired with same timestamp
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "expired"
            assert resp.json()["expired_at"] == first_expired_at

            # Third GET - same result
            resp = await client.get(f"/tasks/{task_id}")
            assert resp.status_code == 200
            assert resp.json()["status"] == "expired"
            assert resp.json()["expired_at"] == first_expired_at

    # -----------------------------------------------------------------------
    # LIFE-12  Review period race: first action wins
    # -----------------------------------------------------------------------
    @pytest.mark.unit
    async def test_review_period_race_first_wins(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """LIFE-12: Review period race -- both dispute and approve available, first wins."""
        # Setup task in review (submitted status)
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Verify task is in submitted status
        resp = await client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"

        # Alice approves first -- this should succeed
        resp = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        # Alice tries to dispute after approval -- should fail with 409
        resp = await file_dispute(client, alice_keypair, alice_agent_id, task_id)
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"
