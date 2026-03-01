from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_non_platform_cannot_release_escrow(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot release escrow."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster PA1", balance=5000)
        worker = await make_funded_agent(name="Worker PA1", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Platform auth test",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to release escrow to themselves
        release_token = worker._sign_jws(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": worker.agent_id,
            }
        )
        response = await worker._request_raw(
            "POST",
            f"{worker.config.bank_url}/escrow/{escrow_id}/release",
            json={"token": release_token},
        )

        assert response.status_code == 403, (
            f"Non-platform escrow release should return 403, got {response.status_code}"
        )

        # Verify escrow is still locked — poster balance unchanged
        poster_balance = await poster.get_balance()
        assert poster_balance["balance"] == 4500, "Escrow should still be locked"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_platform_cannot_split_escrow(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot split escrow."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster PA2", balance=5000)
        worker = await make_funded_agent(name="Worker PA2", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Platform split auth test",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Work")
        await worker.submit_deliverable(task["task_id"])
        await poster.dispute_task(task_id=task["task_id"], reason="Bad work")

        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to split escrow 100% to themselves
        split_token = worker._sign_jws(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker.agent_id,
                "poster_account_id": poster.agent_id,
                "worker_pct": 100,
            }
        )
        response = await worker._request_raw(
            "POST",
            f"{worker.config.bank_url}/escrow/{escrow_id}/split",
            json={"token": split_token},
        )

        assert response.status_code == 403, (
            f"Non-platform escrow split should return 403, got {response.status_code}"
        )

        # Verify balances unchanged
        worker_balance = await worker.get_balance()
        assert worker_balance["balance"] == 0, "Worker should not have received funds"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_platform_cannot_credit_account(make_funded_agent) -> None:
    """Adversarial: non-platform agent cannot credit another agent's account."""
    agents_to_close: list[BaseAgent] = []

    try:
        agent_a = await make_funded_agent(name="Agent PA3 A", balance=100)
        agent_b = await make_funded_agent(name="Agent PA3 B", balance=0)
        agents_to_close.extend([agent_a, agent_b])

        # Agent A (non-platform) tries to credit Agent B
        credit_token = agent_a._sign_jws(
            {
                "action": "credit",
                "account_id": agent_b.agent_id,
                "amount": 500,
                "reference": "fake_salary",
            }
        )
        response = await agent_a._request_raw(
            "POST",
            f"{agent_a.config.bank_url}/accounts/{agent_b.agent_id}/credit",
            json={"token": credit_token},
        )

        assert response.status_code == 403, (
            f"Non-platform credit should return 403, got {response.status_code}"
        )

        # Verify B's balance unchanged
        b_balance = await agent_b.get_balance()
        assert b_balance["balance"] == 0, "Agent B should not have received fraudulent credit"

        # Verify A's balance unchanged
        a_balance = await agent_a.get_balance()
        assert a_balance["balance"] == 100, "Agent A balance should be unchanged"
    finally:
        await _close_agents(agents_to_close)
