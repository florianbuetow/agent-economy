from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


async def _create_disputed_task(
    poster: BaseAgent, worker: BaseAgent, reward: int = 1000
) -> tuple[dict[str, Any], str]:
    """Drive a task through the full lifecycle to disputed state."""
    task = await poster.post_task(
        title="Court ruling task",
        spec="Implement the feature as specified",
        reward=reward,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    bid = await worker.submit_bid(task_id=task["task_id"], amount=reward)
    await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
    await worker.upload_asset(task["task_id"], "deliverable.txt", b"Implementation complete")
    await worker.submit_deliverable(task["task_id"])

    dispute_reason = "Deliverable does not meet specification requirements"
    await poster.dispute_task(task_id=task["task_id"], reason=dispute_reason)

    disputed_task = await poster.get_task(task["task_id"])
    assert disputed_task["status"] == "disputed"
    return disputed_task, dispute_reason


async def _file_dispute_with_court(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    disputed_task: dict[str, Any],
    dispute_reason: str,
) -> str:
    """File the dispute with the Court service via the platform agent.

    Returns the dispute_id. Fails the test if filing cannot be completed.
    """
    task_id = str(disputed_task["task_id"])

    # Check if Court already has a dispute for this task
    disputes = await poster.list_disputes(task_id=task_id)
    if len(disputes) > 0:
        return str(disputes[0]["dispute_id"])

    try:
        file_response = await platform_agent.file_claim(
            task_id=task_id,
            claimant_id=str(poster.agent_id),
            respondent_id=str(worker.agent_id),
            claim=dispute_reason,
            escrow_id=str(disputed_task["escrow_id"]),
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            pytest.fail(
                f"Court dispute filing failed with status "
                f"{exc.response.status_code}: {exc.response.text}"
            )
        refreshed_disputes = await poster.list_disputes(task_id=task_id)
        if len(refreshed_disputes) > 0:
            return str(refreshed_disputes[0]["dispute_id"])
        pytest.fail("Court dispute filing returned 409 but no dispute was listed")

    return str(file_response["dispute_id"])


async def _submit_rebuttal(platform_agent: PlatformAgent, dispute_id: str) -> int:
    """Submit a rebuttal on behalf of the worker. Returns HTTP status code."""
    try:
        await platform_agent.submit_rebuttal(
            dispute_id,
            "The deliverable meets all specification requirements.",
        )
    except httpx.HTTPStatusError as exc:
        return exc.response.status_code
    return 200


async def _trigger_ruling(
    platform_agent: PlatformAgent, dispute_id: str
) -> tuple[int, dict[str, Any]]:
    """Trigger the judge panel ruling. Returns (status_code, response_json)."""
    try:
        payload = await platform_agent.trigger_ruling(dispute_id)
    except httpx.HTTPStatusError as exc:
        error_payload: dict[str, Any] = {}
        if exc.response.headers.get("content-type", "").startswith("application/json"):
            error_payload = exc.response.json()
        return exc.response.status_code, error_payload
    return 200, payload


async def _file_duplicate_dispute(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    disputed_task: dict[str, Any],
) -> int:
    try:
        await platform_agent.file_claim(
            task_id=str(disputed_task["task_id"]),
            claimant_id=str(poster.agent_id),
            respondent_id=str(worker.agent_id),
            claim="Filing again",
            escrow_id=str(disputed_task["escrow_id"]),
        )
    except httpx.HTTPStatusError as exc:
        return exc.response.status_code
    return 201


async def _drive_to_ruling(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    reward: int = 1000,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Full lifecycle: post -> bid -> accept -> deliver -> dispute -> court ruling.

    Returns (disputed_task, dispute_id, ruling_payload).
    Skips the test if court is unavailable or ruling fails.
    """
    disputed_task, dispute_reason = await _create_disputed_task(poster, worker, reward=reward)

    dispute_id = await _file_dispute_with_court(
        poster, worker, platform_agent, disputed_task, dispute_reason
    )

    rebuttal_status = await _submit_rebuttal(platform_agent, dispute_id)
    assert rebuttal_status in {200, 409}, f"Unexpected rebuttal status: {rebuttal_status}"

    ruling_status, ruling_payload = await _trigger_ruling(platform_agent, dispute_id)
    assert ruling_status == 200, f"Court ruling failed with status {ruling_status}"

    assert ruling_payload["status"] == "ruled"
    return disputed_task, dispute_id, ruling_payload


@pytest.mark.e2e
async def test_escrow_split_proportional_payout(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm escrow is split proportionally after court ruling — not just sum check."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR1", balance=5000)
        worker = await make_funded_agent(name="Worker CR1", balance=0)
        agents_to_close.extend([poster, worker])

        reward = 1000
        _disputed_task, _dispute_id, ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=reward
        )

        worker_pct = ruling_payload["worker_pct"]
        assert isinstance(worker_pct, int)
        assert 0 <= worker_pct <= 100

        # Verify proportional split — not just that sum is correct
        expected_worker_amount = (reward * worker_pct) // 100
        expected_poster_amount = reward - expected_worker_amount

        worker_balance = await worker.get_balance()
        poster_balance = await poster.get_balance()

        assert worker_balance["balance"] == expected_worker_amount, (
            f"Worker should get {expected_worker_amount} ({worker_pct}% of {reward}), "
            f"got {worker_balance['balance']}"
        )
        assert poster_balance["balance"] == 5000 - reward + expected_poster_amount, (
            f"Poster should get {expected_poster_amount} back from escrow, "
            f"got {poster_balance['balance']} (expected {5000 - reward + expected_poster_amount})"
        )

        # Conservation check
        assert poster_balance["balance"] + worker_balance["balance"] == 5000
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_ruling_recorded_on_task_board(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm task record is updated with ruling details after court decision."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR2", balance=5000)
        worker = await make_funded_agent(name="Worker CR2", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, _dispute_id, ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=1000
        )

        # Check the task on TaskBoard has ruling details
        task_after = await poster.get_task(str(disputed_task["task_id"]))
        assert task_after["status"] == "ruled", (
            f"Task status should be 'ruled', got '{task_after['status']}'"
        )
        assert isinstance(task_after.get("ruling_id"), str), "ruling_id should be set"
        assert task_after["ruling_id"] != "", "ruling_id should not be empty"
        assert isinstance(task_after.get("worker_pct"), int), "worker_pct should be set"
        assert task_after["worker_pct"] == ruling_payload["worker_pct"], (
            "worker_pct should match Court ruling"
        )
        assert isinstance(task_after.get("ruling_summary"), str), "ruling_summary should be set"
        assert task_after["ruling_summary"] != "", "ruling_summary should not be empty"
        assert task_after.get("ruled_at") is not None, "ruled_at timestamp should be set"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_court_posts_reputation_feedback(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Confirm Court posts feedback to Reputation service for both parties after ruling."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR3", balance=5000)
        worker = await make_funded_agent(name="Worker CR3", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, _dispute_id, _ruling_payload = await _drive_to_ruling(
            poster, worker, platform_agent, reward=1000
        )

        # Court should have posted two feedback records to Reputation
        task_feedback = await poster.get_task_feedback(str(disputed_task["task_id"]))
        assert len(task_feedback) >= 2, (
            f"Expected at least 2 feedback records from Court, got {len(task_feedback)}"
        )

        # Check for spec_quality feedback targeting the poster
        spec_feedback = [
            fb
            for fb in task_feedback
            if fb["category"] == "spec_quality" and fb["to_agent_id"] == poster.agent_id
        ]
        assert len(spec_feedback) >= 1, "Court should post spec_quality feedback for poster"

        # Check for delivery_quality feedback targeting the worker
        delivery_feedback = [
            fb
            for fb in task_feedback
            if fb["category"] == "delivery_quality" and fb["to_agent_id"] == worker.agent_id
        ]
        assert len(delivery_feedback) >= 1, "Court should post delivery_quality feedback for worker"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_dispute_proceeds_without_rebuttal(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Edge case: ruling should proceed even without worker rebuttal."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR4", balance=5000)
        worker = await make_funded_agent(name="Worker CR4", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_reason = await _create_disputed_task(poster, worker, reward=1000)

        dispute_id = await _file_dispute_with_court(
            poster, worker, platform_agent, disputed_task, dispute_reason
        )

        # Skip rebuttal entirely — go straight to ruling
        ruling_status, ruling_payload = await _trigger_ruling(platform_agent, dispute_id)

        assert ruling_status == 200, (
            f"Court ruling without rebuttal failed with status {ruling_status}"
        )
        assert ruling_payload["status"] == "ruled"
        assert isinstance(ruling_payload.get("worker_pct"), int)
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_duplicate_dispute_rejected(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Adversarial: filing a second dispute on an already-disputed task is rejected."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CR5", balance=5000)
        worker = await make_funded_agent(name="Worker CR5", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_reason = await _create_disputed_task(poster, worker, reward=1000)

        # First filing should succeed (or already exist)
        _dispute_id = await _file_dispute_with_court(
            poster, worker, platform_agent, disputed_task, dispute_reason
        )

        # Second filing should be rejected
        duplicate_status = await _file_duplicate_dispute(
            poster,
            worker,
            platform_agent,
            disputed_task,
        )

        assert duplicate_status == 409
    finally:
        await _close_agents(agents_to_close)
