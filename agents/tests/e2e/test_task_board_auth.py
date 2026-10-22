from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from .native_task_contract import lifecycle_version, new_operation_id, publish_task

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_self_bid_rejected(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster QFH", balance=5000)
        agents_to_close.append(poster)

        task = await publish_task(
            poster,
            title="Self bid rejected task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        response = await poster._request_raw(
            "POST",
            f"{poster.config.task_board_url}/tasks/{task['task_id']}/bids",
            json={
                "token": poster._sign_jws(
                    {
                        "action": "submit_bid",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(task),
                        "task_id": task["task_id"],
                        "bidder_id": poster.agent_id,
                        "amount": 400,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/bids",
                )
            },
        )

        assert response.status_code == 400
        assert response.json()["error"] == "self_bid"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_duplicate_bid_rejected(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster HZM", balance=5000)
        worker = await make_funded_agent(name="Worker HZM", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Duplicate bid rejected task",
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

        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/bids",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "submit_bid",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(task),
                        "task_id": task["task_id"],
                        "bidder_id": worker.agent_id,
                        "amount": 350,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/bids",
                )
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "bid_already_exists"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_poster_cannot_cancel_task(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster PLK", balance=5000)
        worker = await make_funded_agent(name="Worker PLK", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Non poster cannot cancel task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/cancel",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "cancel_task",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(task),
                        "task_id": task["task_id"],
                        "poster_id": worker.agent_id,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/cancel",
                )
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_poster_cannot_accept_bid(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster VTR", balance=5000)
        worker1 = await make_funded_agent(name="Worker VTR 1", balance=0)
        worker2 = await make_funded_agent(name="Worker VTR 2", balance=0)
        agents_to_close.extend([poster, worker1, worker2])

        task = await publish_task(
            poster,
            title="Non poster cannot accept bid task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid_response = await worker1.submit_bid(
            task_id=task["task_id"],
            amount=400,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert bid_response.status_code == 201
        bid = bid_response.body

        response = await worker2._request_raw(
            "POST",
            f"{worker2.config.task_board_url}/tasks/{task['task_id']}/bids/{bid['bid_id']}/accept",
            json={
                "token": worker2._sign_jws(
                    {
                        "action": "accept_bid",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(task),
                        "task_id": task["task_id"],
                        "bid_id": bid["bid_id"],
                        "poster_id": worker2.agent_id,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/bids/{bid['bid_id']}/accept",
                )
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_poster_cannot_approve_task(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster JNS", balance=5000)
        worker = await make_funded_agent(name="Worker JNS", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Non poster cannot approve task",
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
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
        submit_response = await worker.submit_deliverable(
            task["task_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(accepted_task),
        )
        assert submit_response.status_code == 200
        submitted_task = submit_response.body
        assert submitted_task["status"] == "submitted"

        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/approve",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "approve_task",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(submitted_task),
                        "task_id": task["task_id"],
                        "poster_id": worker.agent_id,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/approve",
                )
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_poster_cannot_dispute_task(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster MCD", balance=5000)
        worker = await make_funded_agent(name="Worker MCD", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Non poster cannot dispute task",
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
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
        submit_response = await worker.submit_deliverable(
            task["task_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(accepted_task),
        )
        assert submit_response.status_code == 200
        submitted_task = submit_response.body
        assert submitted_task["status"] == "submitted"

        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/dispute",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "dispute_task",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(submitted_task),
                        "task_id": task["task_id"],
                        "poster_id": worker.agent_id,
                        "reason": "Bad work",
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/dispute",
                )
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_worker_cannot_upload_asset(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster YUP", balance=5000)
        worker = await make_funded_agent(name="Worker YUP", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Non worker cannot upload asset",
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

        response = await poster._request_raw(
            "POST",
            f"{poster.config.task_board_url}/tasks/{task['task_id']}/assets",
            headers=poster._auth_header(
                {
                    "action": "upload_asset",
                    "task_id": task["task_id"],
                },
                audience="task-board",
                resource=f"POST /tasks/{task['task_id']}/assets",
            ),
            files={"file": ("test.txt", b"data")},
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_non_worker_cannot_submit_deliverable(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster AWE", balance=5000)
        worker = await make_funded_agent(name="Worker AWE", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Non worker cannot submit deliverable",
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
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")

        response = await poster._request_raw(
            "POST",
            f"{poster.config.task_board_url}/tasks/{task['task_id']}/submit",
            json={
                "token": poster._sign_jws(
                    {
                        "action": "submit_deliverable",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(accepted_task),
                        "task_id": task["task_id"],
                        "worker_id": poster.agent_id,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/submit",
                )
            },
        )

        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_approve_non_submitted_task_rejected(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster KRT", balance=5000)
        worker = await make_funded_agent(name="Worker KRT", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Approve non submitted task rejected",
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

        response = await poster._request_raw(
            "POST",
            f"{poster.config.task_board_url}/tasks/{task['task_id']}/approve",
            json={
                "token": poster._sign_jws(
                    {
                        "action": "approve_task",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(accepted_task),
                        "task_id": task["task_id"],
                        "poster_id": poster.agent_id,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/approve",
                )
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_bidding_deadline_expiry_rejects_bid(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster BDX", balance=5000)
        worker = await make_funded_agent(name="Worker BDX", balance=0)
        agents_to_close.extend([poster, worker])

        task = await publish_task(
            poster,
            title="Bidding deadline expiry rejects bid task",
            spec="Do something quickly",
            reward=500,
            bidding_deadline_seconds=2,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        await asyncio.sleep(5)

        expired_task = await poster.get_task(task["task_id"])
        assert expired_task["status"] == "expired"

        response = await worker._request_raw(
            "POST",
            f"{worker.config.task_board_url}/tasks/{task['task_id']}/bids",
            json={
                "token": worker._sign_jws(
                    {
                        "action": "submit_bid",
                        "operation_id": new_operation_id(),
                        "expected_lifecycle_version": lifecycle_version(expired_task),
                        "task_id": task["task_id"],
                        "bidder_id": worker.agent_id,
                        "amount": 400,
                    },
                    audience="task-board",
                    resource=f"POST /tasks/{task['task_id']}/bids",
                )
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "invalid_status"
    finally:
        await _close_agents(agents_to_close)
