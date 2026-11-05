"""ECO-04: independent workers compete across concurrently published jobs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from .native_task_contract import lifecycle_version, new_operation_id, publish_task


@pytest.mark.e2e
async def test_five_workers_and_two_posters_keep_bids_and_payments_task_bound(
    make_funded_agent: Any,
) -> None:
    actors: list[Any] = []
    try:
        posters = []
        workers = []
        for index in range(2):
            agent = await make_funded_agent(name=f"Burst poster {index}", balance=1000)
            posters.append(agent)
            actors.append(agent)
        for index in range(5):
            agent = await make_funded_agent(name=f"Burst worker {index}", balance=0)
            workers.append(agent)
            actors.append(agent)
        assert len({actor.agent_id for actor in actors}) == 7

        tasks = await asyncio.gather(
            *(
                publish_task(
                    posters[index % 2],
                    title=f"Independent burst job {index}",
                    spec=f"Deliver job-{index}.txt containing exact text result-{index}.",
                    reward=100,
                    bidding_deadline_seconds=3600,
                    acceptance_window_seconds=3600,
                    execution_deadline_seconds=7200,
                    review_deadline_seconds=3600,
                )
                for index in range(5)
            )
        )
        assert len({task["task_id"] for task in tasks}) == 5
        all_bids = await asyncio.gather(
            *(
                worker.submit_bid(
                    task_id=task["task_id"],
                    amount=20 + worker_index,
                    operation_id=new_operation_id(),
                    expected_lifecycle_version=lifecycle_version(task),
                )
                for task in tasks
                for worker_index, worker in enumerate(workers)
            )
        )
        assert all(bid.status_code == 201 for bid in all_bids)
        assert len({bid.body["bid_id"] for bid in all_bids}) == 25
        for task in tasks:
            with pytest.raises(httpx.HTTPStatusError) as sealed:
                await workers[0].list_bids(task["task_id"])
            assert sealed.value.response.status_code == 403

        # Explicit choices deliberately differ from cheapest/first-arriving bids.
        choices = [4, 3, 2, 1, 0]
        accepted = await asyncio.gather(
            *(
                posters[index % 2].accept_bid(
                    task_id=task["task_id"],
                    bid_id=all_bids[index * 5 + choices[index]].body["bid_id"],
                    operation_id=new_operation_id(),
                    expected_lifecycle_version=lifecycle_version(task),
                )
                for index, task in enumerate(tasks)
            )
        )
        for index, response in enumerate(accepted):
            assert response.status_code == 200
            assert response.body["worker_id"] == workers[choices[index]].agent_id
            assert (
                response.body["accepted_bid_id"]
                == all_bids[index * 5 + choices[index]].body["bid_id"]
            )

        async def deliver(index: int) -> Any:
            worker = workers[choices[index]]
            task_id = tasks[index]["task_id"]
            await worker.upload_asset(
                task_id,
                f"job-{index}.txt",
                f"result-{index}".encode(),
            )
            return await worker.submit_deliverable(
                task_id,
                operation_id=new_operation_id(),
                expected_lifecycle_version=lifecycle_version(accepted[index].body),
            )

        submitted = await asyncio.gather(*(deliver(index) for index in range(5)))
        assert all(response.status_code == 200 for response in submitted)
        approved = await asyncio.gather(
            *(
                posters[index % 2].approve_task(
                    task["task_id"],
                    operation_id=new_operation_id(),
                    expected_lifecycle_version=lifecycle_version(submitted[index].body),
                )
                for index, task in enumerate(tasks)
            )
        )
        assert all(response.status_code == 200 for response in approved)
        for index, task in enumerate(tasks):
            final = await workers[choices[index]].get_task(task["task_id"])
            assert final["status"] == "approved"
            assert final["poster_id"] == posters[index % 2].agent_id
            assert final["worker_id"] == workers[choices[index]].agent_id
            assert final["accepted_bid_amount"] == 20 + choices[index]
            assert final["worker_payment"] == 20 + choices[index]
            assert final["poster_refund"] == 80 - choices[index]
        balances = [int((await actor.get_balance())["balance"]) for actor in actors]
        assert balances[:2] == [934, 956]
        assert balances[2:] == [20, 21, 22, 23, 24]
        assert sum(balances) == 2000
    finally:
        for actor in actors:
            await actor.close()
