from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_auth.financial_identity import operation_id_for

from .native_task_contract import lifecycle_version, new_operation_id

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
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to release escrow to themselves
        release_token = worker._sign_jws(
            {
                "action": "escrow_release",
                "operation_id": operation_id_for(
                    "e2e-unauthorized-release", (escrow_id, worker.agent_id)
                ),
                "source_reference": f"task:{task['task_id']}:unauthorized-release",
                "escrow_id": escrow_id,
                "recipient_account_id": worker.agent_id,
            },
            audience="central-bank",
            resource=f"POST /escrow/{escrow_id}/release",
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
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid_response = await worker.submit_bid(
            task_id=task["task_id"],
            amount=400,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert bid_response.status_code == 201
        bid = bid_response.body
        accept_response = await poster.accept_bid(
            task_id=task["task_id"],
            bid_id=bid["bid_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert accept_response.status_code == 200
        accepted_task = accept_response.body
        assert accepted_task["status"] == "accepted"
        await worker.upload_asset(task["task_id"], "result.txt", b"Work")
        submit_response = await worker.submit_deliverable(
            task["task_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(accepted_task),
        )
        assert submit_response.status_code == 200
        submitted_task = submit_response.body
        assert submitted_task["status"] == "submitted"
        dispute_response = await poster.dispute_task(
            task_id=task["task_id"],
            reason="Bad work",
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(submitted_task),
        )
        assert dispute_response.status_code == 200
        disputed_command_task = dispute_response.body
        assert disputed_command_task["status"] == "disputed"

        escrow_id = task["escrow_id"]

        # Worker (non-platform) tries to split escrow 100% to themselves
        split_token = worker._sign_jws(
            {
                "action": "escrow_split",
                "operation_id": operation_id_for(
                    "e2e-unauthorized-split", (escrow_id, worker.agent_id)
                ),
                "source_reference": f"task:{task['task_id']}:unauthorized-split",
                "escrow_id": escrow_id,
                "worker_account_id": worker.agent_id,
                "poster_account_id": poster.agent_id,
                "worker_pct": 100,
            },
            audience="central-bank",
            resource=f"POST /escrow/{escrow_id}/split",
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
                "operation_id": operation_id_for(
                    "e2e-unauthorized-credit", (agent_b.agent_id, "fake_salary")
                ),
                "account_id": agent_b.agent_id,
                "amount": 500,
                "reference": "fake_salary",
            },
            audience="central-bank",
            resource=f"POST /accounts/{agent_b.agent_id}/credit",
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
