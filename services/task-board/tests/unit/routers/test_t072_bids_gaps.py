"""T-072 coverage-sweep gap fixes for bid submission, listing, and acceptance.

Each test below closes a spec ID from ``docs/specifications/service-tests/
task-board-service-tests.md`` that the existing ``test_bids.py`` suite does not
actually exercise under its own drifted ``BID-nn``/``BL-nn``/``BA-nn`` docstring
labels. These are new tests only -- no existing test file is modified.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    create_task,
    make_task_id,
    submit_bid,
)

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestBiddingGaps:
    """Gaps in Category 4: Bidding (POST /tasks/{task_id}/bids)."""

    @pytest.mark.unit
    async def test_bid13_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """BID-13: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)
        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        bid_payload = {
            "action": "submit_bid",
            "task_id": task_id_2,
            "bidder_id": bob_agent_id,
            "amount": 90,
        }
        token = make_jws_token(bob_keypair[0], bob_agent_id, bid_payload)
        resp = await client.post(f"/tasks/{task_id_1}/bids", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"


class TestBidListingGaps:
    """Gaps in Category 5: Bid Listing (GET /tasks/{task_id}/bids)."""

    @pytest.mark.unit
    async def test_bl05_bids_public_for_cancelled_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """BL-05: Bids are public (no auth) for a CANCELLED task."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        await submit_bid(client, bob_keypair, bob_agent_id, task_id)

        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(alice_keypair[0], alice_agent_id, cancel_payload)
        cancel_resp = await client.post(f"/tasks/{task_id}/cancel", json={"token": cancel_token})
        assert cancel_resp.status_code == 200

        # No Authorization header — task is CANCELLED, bids must be public.
        resp = await client.get(f"/tasks/{task_id}/bids")
        assert resp.status_code == 200
        assert len(resp.json()["bids"]) == 1

    @pytest.mark.unit
    async def test_bl06_bids_public_for_expired_task(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """BL-06: Bids are public (no auth) for a lazily-EXPIRED task."""
        task_id = make_task_id()

        with freeze_time("2025-01-01 00:00:00") as frozen:
            await create_task(
                client,
                alice_keypair,
                alice_agent_id,
                task_id=task_id,
                bidding_deadline_seconds=1,
            )
            await submit_bid(client, bob_keypair, bob_agent_id, task_id)

            # Advance past the bidding deadline so lazy evaluation expires the task.
            frozen.tick(delta=datetime.timedelta(seconds=2))

            # No Authorization header — task should now be lazily EXPIRED, bids public.
            resp = await client.get(f"/tasks/{task_id}/bids")
            assert resp.status_code == 200
            assert len(resp.json()["bids"]) == 1

            task_resp = await client.get(f"/tasks/{task_id}")
            assert task_resp.json()["status"] == "expired"


class TestBidAcceptanceGaps:
    """Gaps in Category 6: Bid Acceptance (POST /tasks/{task_id}/bids/{bid_id}/accept)."""

    @pytest.mark.unit
    async def test_ba08_bid_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """BA-08: bid_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id)
        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
        real_bid_id = bid_resp.json()["bid_id"]

        accept_payload = {
            "action": "accept_bid",
            "task_id": task_id,
            "bid_id": "bid-00000000-0000-0000-0000-000000000000",
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(alice_keypair[0], alice_agent_id, accept_payload)
        resp = await client.post(
            f"/tasks/{task_id}/bids/{real_bid_id}/accept", json={"token": token}
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_ba09_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
    ) -> None:
        """BA-09: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)
        bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id_1)
        bid_id = bid_resp.json()["bid_id"]

        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        accept_payload = {
            "action": "accept_bid",
            "task_id": task_id_2,
            "bid_id": bid_id,
            "poster_id": alice_agent_id,
        }
        token = make_jws_token(alice_keypair[0], alice_agent_id, accept_payload)
        resp = await client.post(f"/tasks/{task_id_1}/bids/{bid_id}/accept", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"
