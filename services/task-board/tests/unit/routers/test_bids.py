"""Bid submission, listing, and acceptance tests for Task Board service."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    accept_bid,
    create_task,
    make_task_id,
    submit_bid,
)


class TestBidding:
    """Category 4: Bidding (POST /tasks/{task_id}/bids) — BID-01 to BID-15."""

    @pytest.mark.unit
    async def test_bid_01_submit_valid_bid(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-01: Submit a valid bid returns 201 with bid details."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        response = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert response.status_code == 201

        data = response.json()
        assert "bid_id" in data
        assert data["bid_id"].startswith("bid-")
        assert data["bidder_id"] == bob_agent_id
        assert "amount" in data
        assert "submitted_at" in data
        datetime.fromisoformat(data["submitted_at"])

    @pytest.mark.unit
    async def test_bid_02_bid_on_nonexistent_task(
        self,
        client,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-02: Bid on a nonexistent task returns 404 TASK_NOT_FOUND."""
        fake_task_id = make_task_id()
        response = await submit_bid(client, bob_keypair, bob_agent_id, fake_task_id)
        assert response.status_code == 404
        assert response.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_bid_03_bid_on_cancelled_task(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-03: Bid on a cancelled task returns 409 INVALID_STATUS."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Cancel the task
        private_key = alice_keypair[0]
        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(private_key, alice_agent_id, cancel_payload)
        cancel_resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert cancel_resp.status_code == 200

        response = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_bid_04_bid_on_accepted_task(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BID-04: Bid on an accepted task returns 409 INVALID_STATUS."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        accept_resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert accept_resp.status_code == 200

        response = await submit_bid(client, carol_keypair, carol_agent_id, task_id)
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_bid_05_duplicate_bid_rejected(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-05: Duplicate bid from same agent returns 409 BID_ALREADY_EXISTS."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        first = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert first.status_code == 201

        second = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert second.status_code == 409
        assert second.json()["error"] == "BID_ALREADY_EXISTS"

    @pytest.mark.unit
    async def test_bid_06_multiple_different_bidders(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BID-06: Multiple different agents can bid on the same task."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bob_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bob_resp.status_code == 201

        carol_resp = await submit_bid(client, carol_keypair, carol_agent_id, task_id)
        assert carol_resp.status_code == 201

        assert bob_resp.json()["bid_id"] != carol_resp.json()["bid_id"]

    @pytest.mark.unit
    async def test_bid_07_signer_mismatch(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_agent_id,
    ):
        """BID-07: Signer does not match bidder_id returns 403 FORBIDDEN."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Bob signs but claims to be Carol
        private_key = bob_keypair[0]
        payload = {
            "action": "submit_bid",
            "task_id": task_id,
            "bidder_id": carol_agent_id,
            "amount": 90,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        response = await client.post(f"/tasks/{task_id}/bids", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_bid_08_wrong_action(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-08: Wrong action in bid token returns 400 INVALID_PAYLOAD."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        private_key = bob_keypair[0]
        payload = {
            "action": "create_task",
            "task_id": task_id,
            "bidder_id": bob_agent_id,
            "amount": 90,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        response = await client.post(f"/tasks/{task_id}/bids", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_bid_09_missing_payload_fields(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-09: Missing required payload fields returns 400 INVALID_PAYLOAD."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Missing bidder_id and amount
        private_key = bob_keypair[0]
        payload = {
            "action": "submit_bid",
            "task_id": task_id,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        response = await client.post(f"/tasks/{task_id}/bids", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "invalid_amount",
        [0, -1, 3.5, "abc"],
        ids=["zero", "negative", "float", "string"],
    )
    async def test_bid_10_invalid_bid_amount(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        invalid_amount,
    ):
        """BID-10: Invalid bid amount returns 400 INVALID_REWARD."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        private_key = bob_keypair[0]
        payload = {
            "action": "submit_bid",
            "task_id": task_id,
            "bidder_id": bob_agent_id,
            "amount": invalid_amount,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)
        response = await client.post(f"/tasks/{task_id}/bids", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_REWARD"

    @pytest.mark.unit
    async def test_bid_11_self_bid_rejected(
        self,
        client,
        alice_keypair,
        alice_agent_id,
    ):
        """BID-11: Poster bidding on own task returns 400 SELF_BID."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        response = await submit_bid(client, alice_keypair, alice_agent_id, task_id)
        assert response.status_code == 400
        assert response.json()["error"] == "SELF_BID"

    @pytest.mark.unit
    async def test_bid_12_bid_after_bidding_deadline(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-12: Bid after bidding deadline expired returns 409 INVALID_STATUS."""
        task_resp = await create_task(
            client,
            alice_keypair,
            alice_agent_id,
            bidding_deadline_seconds=1,
        )
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Wait for the bidding deadline to pass
        await asyncio.sleep(1.5)

        response = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_bid_13_concurrent_duplicate_bid_race(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-13: Concurrent duplicate bid race — one 201, one 409."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        results = await asyncio.gather(
            submit_bid(client, bob_keypair, bob_agent_id, task_id),
            submit_bid(client, bob_keypair, bob_agent_id, task_id),
        )

        status_codes = sorted([r.status_code for r in results])
        assert status_codes == [201, 409]

        error_responses = [r for r in results if r.status_code == 409]
        assert error_responses[0].json()["error"] == "BID_ALREADY_EXISTS"

    @pytest.mark.unit
    async def test_bid_14_bid_increments_bid_count(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BID-14: Submitting a bid increments bid_count on the task."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]
        assert task_resp.json()["bid_count"] == 0

        await submit_bid(client, bob_keypair, bob_agent_id, task_id)

        task_get = await client.get(f"/tasks/{task_id}")
        assert task_get.status_code == 200
        assert task_get.json()["bid_count"] == 1

    @pytest.mark.unit
    async def test_bid_15_malformed_bid_token(
        self,
        client,
        alice_keypair,
        alice_agent_id,
    ):
        """BID-15: Malformed bid token returns 400 INVALID_JWS."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        response = await client.post(f"/tasks/{task_id}/bids", json={"token": "not-a-valid-jws"})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


class TestBidListing:
    """Category 5: Bid Listing (GET /tasks/{task_id}/bids) — BL-01 to BL-08."""

    @pytest.mark.unit
    async def test_bl_01_list_bids_with_bids(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BL-01: List bids returns 200 with bids array when bids exist."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201

        # Poster lists bids with auth during OPEN phase
        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 200

        data = response.json()
        assert "bids" in data
        assert isinstance(data["bids"], list)
        assert len(data["bids"]) == 1

    @pytest.mark.unit
    async def test_bl_02_list_bids_empty(
        self,
        client,
        alice_keypair,
        alice_agent_id,
    ):
        """BL-02: List bids returns 200 with empty array when no bids."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Poster lists bids with auth
        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 200
        assert response.json()["bids"] == []

    @pytest.mark.unit
    async def test_bl_03_list_bids_nonexistent_task(
        self,
        client,
    ):
        """BL-03: List bids on nonexistent task returns 404."""
        fake_task_id = make_task_id()
        response = await client.get(f"/tasks/{fake_task_id}/bids")
        assert response.status_code == 404
        assert response.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_bl_04_sealed_bids_poster_sees_with_bearer(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BL-04: During OPEN status, poster can see sealed bids via Bearer auth."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 200

        bids = response.json()["bids"]
        assert len(bids) == 1
        assert bids[0]["bid_id"] == bid_id
        assert bids[0]["bidder_id"] == bob_agent_id

    @pytest.mark.unit
    async def test_bl_05_bids_public_after_acceptance(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BL-05: After OPEN status (accepted), bids are public without auth."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)

        # No auth needed after acceptance
        response = await client.get(f"/tasks/{task_id}/bids")
        assert response.status_code == 200
        assert len(response.json()["bids"]) == 1

    @pytest.mark.unit
    async def test_bl_06_bid_list_includes_fields(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BL-06: Bid list entries include expected fields."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        await submit_bid(client, bob_keypair, bob_agent_id, task_id)

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 200

        bid = response.json()["bids"][0]
        assert "bid_id" in bid
        assert "bidder_id" in bid
        assert "amount" in bid
        assert "submitted_at" in bid

    @pytest.mark.unit
    async def test_bl_07_ordered_by_submitted_at(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BL-07: Bids are ordered by submitted_at."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        await submit_bid(client, carol_keypair, carol_agent_id, task_id)

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 200

        bids = response.json()["bids"]
        assert len(bids) == 2
        timestamps = [b["submitted_at"] for b in bids]
        assert timestamps == sorted(timestamps)

    @pytest.mark.unit
    async def test_bl_08_non_poster_forbidden_on_sealed_bids(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BL-08: Non-poster gets 403 when trying to list sealed bids."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        await submit_bid(client, bob_keypair, bob_agent_id, task_id)

        # Bob tries to list bids (not the poster)
        private_key = bob_keypair[0]
        token = make_jws_token(
            private_key,
            bob_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}
        response = await client.get(f"/tasks/{task_id}/bids", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"


class TestBidAcceptance:
    """Category 6: Bid Acceptance (POST /tasks/{task_id}/bids/{bid_id}/accept) — BA-01 to BA-10."""

    @pytest.mark.unit
    async def test_ba_01_accept_valid_bid(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BA-01: Accept a valid bid returns 200 with accepted status and worker_id."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "accepted"
        assert data["worker_id"] == bob_agent_id
        assert data["accepted_bid_id"] == bid_id

    @pytest.mark.unit
    async def test_ba_02_accept_nonexistent_bid(
        self,
        client,
        alice_keypair,
        alice_agent_id,
    ):
        """BA-02: Accept a nonexistent bid returns 404 BID_NOT_FOUND."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        fake_bid_id = "bid-00000000-0000-0000-0000-000000000000"
        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, fake_bid_id)
        assert response.status_code == 404
        assert response.json()["error"] == "BID_NOT_FOUND"

    @pytest.mark.unit
    async def test_ba_03_accept_on_nonexistent_task(
        self,
        client,
        alice_keypair,
        alice_agent_id,
    ):
        """BA-03: Accept bid on nonexistent task returns 404 TASK_NOT_FOUND."""
        fake_task_id = make_task_id()
        fake_bid_id = "bid-00000000-0000-0000-0000-000000000000"
        response = await accept_bid(
            client, alice_keypair, alice_agent_id, fake_task_id, fake_bid_id
        )
        assert response.status_code == 404
        assert response.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_ba_04_accept_wrong_status(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BA-04: Accept bid on non-OPEN task returns 409 INVALID_STATUS."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bob_bid = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bob_bid.status_code == 201
        bob_bid_id = bob_bid.json()["bid_id"]

        carol_bid = await submit_bid(client, carol_keypair, carol_agent_id, task_id)
        assert carol_bid.status_code == 201
        carol_bid_id = carol_bid.json()["bid_id"]

        # Accept Bob's bid first
        accept_resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bob_bid_id)
        assert accept_resp.status_code == 200

        # Now try to accept Carol's bid — task is already accepted
        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, carol_bid_id)
        assert response.status_code == 409
        assert response.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_ba_05_non_poster_forbidden(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BA-05: Non-poster cannot accept a bid — returns 403 FORBIDDEN."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        # Carol (not the poster) tries to accept
        response = await accept_bid(client, carol_keypair, carol_agent_id, task_id, bid_id)
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_ba_06_wrong_action(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BA-06: Wrong action in accept token returns 400 INVALID_PAYLOAD."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        private_key = alice_keypair[0]
        payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(private_key, alice_agent_id, payload)
        response = await client.post(
            f"/tasks/{task_id}/bids/{bid_id}/accept", json={"token": token}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_ba_07_signer_mismatch(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    ):
        """BA-07: Signer does not match poster_id returns 403 FORBIDDEN."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        # Carol signs JWS but claims poster_id is Alice (impersonation)
        private_key = carol_keypair[0]
        payload = {
            "action": "accept_bid",
            "task_id": task_id,
            "bid_id": bid_id,
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(private_key, carol_agent_id, payload)
        response = await client.post(
            f"/tasks/{task_id}/bids/{bid_id}/accept", json={"token": token}
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_ba_08_accept_sets_execution_deadline(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BA-08: Accepting a bid sets execution_deadline on the task."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert response.status_code == 200

        data = response.json()
        assert data["execution_deadline"] is not None
        datetime.fromisoformat(data["execution_deadline"])

    @pytest.mark.unit
    async def test_ba_09_accept_sets_accepted_at(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BA-09: Accepting a bid sets accepted_at timestamp."""
        task_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert response.status_code == 200

        data = response.json()
        assert data["accepted_at"] is not None
        datetime.fromisoformat(data["accepted_at"])

    @pytest.mark.unit
    async def test_ba_10_accept_after_bidding_deadline_if_open(
        self,
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
    ):
        """BA-10: Accepting a bid after bidding_deadline still works if task is open."""
        task_resp = await create_task(
            client,
            alice_keypair,
            alice_agent_id,
            bidding_deadline_seconds=1,
        )
        assert task_resp.status_code == 201
        task_id = task_resp.json()["task_id"]

        # Submit bid before deadline
        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        # Wait for deadline to pass
        await asyncio.sleep(1.5)

        # Accept should still work
        response = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
