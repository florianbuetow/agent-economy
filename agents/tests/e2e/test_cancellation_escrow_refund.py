"""GAP-F6/T-073: cancellation escrow refund e2e coverage.

`test_task_board.py::test_post_and_cancel_task` and
`test_task_board.py::test_execution_deadline_expiry` already prove full
escrow refund for (a) cancelling a bid-free open task and (b) execution
deadline expiry after a bid is accepted. The scenario neither covers is
cancelling an OPEN task that already has a pending (not yet accepted) bid:
does the refund still land in full, and does the now-orphaned bid become
unacceptable once the task is cancelled?
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_cancel_task_with_pending_bid_refunds_escrow_and_orphans_bid(
    make_funded_agent,
) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CXR", balance=5000)
        worker = await make_funded_agent(name="Worker CXR", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Cancel task with a pending bid",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        balance_after_post = await poster.get_balance()
        assert balance_after_post["balance"] == 4500

        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)

        await poster.cancel_task(task_id=task["task_id"])

        cancelled_task = await poster.get_task(task["task_id"])
        assert cancelled_task["status"] == "cancelled"

        balance_after_cancel = await poster.get_balance()
        assert balance_after_cancel["balance"] == 5000

        response = await poster._request_raw(
            "POST",
            f"{poster.config.task_board_url}/tasks/{task['task_id']}/bids/{bid['bid_id']}/accept",
            json={
                "token": poster._sign_jws(
                    {
                        "action": "accept_bid",
                        "task_id": task["task_id"],
                        "bid_id": bid["bid_id"],
                        "poster_id": poster.agent_id,
                    }
                )
            },
        )
        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
    finally:
        await _close_agents(agents_to_close)
