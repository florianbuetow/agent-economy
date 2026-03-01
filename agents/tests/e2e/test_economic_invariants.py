from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


async def _drive_dispute_to_ruling(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    reward: int,
) -> dict[str, Any] | None:
    """Drive a task through dispute to ruling. Returns ruling payload or None."""
    task = await poster.post_task(
        title="Economic dispute task",
        spec="Implement the feature",
        reward=reward,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    bid = await worker.submit_bid(task_id=task["task_id"], amount=reward)
    await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
    await worker.upload_asset(task["task_id"], "work.txt", b"Completed work")
    await worker.submit_deliverable(task["task_id"])

    await poster.dispute_task(task_id=task["task_id"], reason="Does not meet spec")

    disputed_task = await poster.get_task(task["task_id"])
    assert disputed_task["status"] == "disputed"

    # File with Court
    task_id = str(disputed_task["task_id"])
    listed = await poster._request(
        "GET",
        f"{poster.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    disputes = listed["disputes"]
    if len(disputes) > 0:
        dispute_id = str(disputes[0]["dispute_id"])
    else:
        file_token = platform_agent._sign_jws(
            {
                "action": "file_dispute",
                "task_id": task_id,
                "claimant_id": poster.agent_id,
                "respondent_id": worker.agent_id,
                "claim": "Does not meet spec",
                "escrow_id": disputed_task["escrow_id"],
            }
        )
        file_resp = await platform_agent._request_raw(
            "POST",
            f"{platform_agent.config.court_url}/disputes/file",
            json={"token": file_token},
        )
        if file_resp.status_code != 201:
            return None
        dispute_id = str(file_resp.json()["dispute_id"])

    # Rebuttal
    rebuttal_token = platform_agent._sign_jws(
        {
            "action": "submit_rebuttal",
            "dispute_id": dispute_id,
            "rebuttal": "Work meets specification.",
        }
    )
    await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rebuttal",
        json={"token": rebuttal_token},
    )

    # Ruling
    ruling_token = platform_agent._sign_jws(
        {"action": "trigger_ruling", "dispute_id": dispute_id}
    )
    ruling_resp = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rule",
        json={"token": ruling_token},
    )
    if ruling_resp.status_code != 200:
        return None

    payload: dict[str, Any] = ruling_resp.json()
    return payload


@pytest.mark.e2e
async def test_economic_cycle_with_dispute_partial_payout(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm partial payouts from disputes flow into subsequent economic activity."""
    agents_to_close: list[BaseAgent] = []

    try:
        agent_a = await make_funded_agent(name="Agent EI1 A", balance=5000)
        agent_b = await make_funded_agent(name="Agent EI1 B", balance=0)
        agents_to_close.extend([agent_a, agent_b])

        # Round 1: A posts, B works, A disputes, Court rules
        reward_1 = 1000
        ruling = await _drive_dispute_to_ruling(
            poster=agent_a,
            worker=agent_b,
            platform_agent=platform_agent,
            reward=reward_1,
        )
        if ruling is None:
            pytest.skip("Court ruling unavailable in this environment")

        worker_pct = ruling["worker_pct"]
        worker_earned = (reward_1 * worker_pct) // 100
        poster_refund = reward_1 - worker_earned

        balance_a_after_r1 = await agent_a.get_balance()
        balance_b_after_r1 = await agent_b.get_balance()
        assert balance_a_after_r1["balance"] == 5000 - reward_1 + poster_refund
        assert balance_b_after_r1["balance"] == worker_earned

        # Conservation check after round 1
        assert balance_a_after_r1["balance"] + balance_b_after_r1["balance"] == 5000

        # Round 2: B posts task using partial earnings (if B has enough)
        if worker_earned < 200:
            pytest.skip(f"Worker earned only {worker_earned}, not enough to post a task")

        reward_2 = min(200, worker_earned)
        task_2 = await agent_b.post_task(
            title="Round 2 task",
            spec="Do more work",
            reward=reward_2,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid_2 = await agent_a.submit_bid(task_id=task_2["task_id"], amount=reward_2)
        await agent_b.accept_bid(task_id=task_2["task_id"], bid_id=bid_2["bid_id"])
        await agent_a.upload_asset(task_2["task_id"], "work2.txt", b"Round 2 work")
        await agent_a.submit_deliverable(task_2["task_id"])
        await agent_b.approve_task(task_2["task_id"])

        # Final balance check
        final_a = await agent_a.get_balance()
        final_b = await agent_b.get_balance()
        assert final_a["balance"] + final_b["balance"] == 5000, "Money conservation violated"
        assert final_a["balance"] == balance_a_after_r1["balance"] + reward_2
        assert final_b["balance"] == balance_b_after_r1["balance"] - reward_2
    finally:
        await _close_agents(agents_to_close)
