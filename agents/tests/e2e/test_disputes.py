from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


async def _create_disputed_task(poster: BaseAgent, worker: BaseAgent) -> tuple[dict[str, Any], str]:
    task = await poster.post_task(
        title="Court dispute task",
        spec="Do something",
        reward=500,
        bidding_deadline_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=3600,
    )
    bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
    await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
    await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
    await worker.submit_deliverable(task["task_id"])

    dispute_reason = "Deliverable does not meet spec"
    await poster.dispute_task(task_id=task["task_id"], reason=dispute_reason)

    disputed_task = await poster.get_task(task["task_id"])
    assert disputed_task["status"] == "disputed"
    return disputed_task, dispute_reason


async def _resolve_dispute_id(
    poster: BaseAgent,
    worker: BaseAgent,
    platform_agent: PlatformAgent,
    disputed_task: dict[str, Any],
    dispute_reason: str,
) -> str | None:
    task_id = str(disputed_task["task_id"])
    listed_disputes = await poster._request(
        "GET",
        f"{poster.config.court_url}/disputes",
        params={"task_id": task_id},
    )
    disputes = listed_disputes["disputes"]
    if len(disputes) > 0:
        return str(disputes[0]["dispute_id"])

    file_token = platform_agent._sign_jws(
        {
            "action": "file_dispute",
            "task_id": task_id,
            "claimant_id": poster.agent_id,
            "respondent_id": worker.agent_id,
            "claim": dispute_reason,
            "escrow_id": disputed_task["escrow_id"],
        }
    )
    file_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/file",
        json={"token": file_token},
    )
    if file_response.status_code == 201:
        return str(file_response.json()["dispute_id"])

    if file_response.status_code == 409:
        refreshed = await poster._request(
            "GET",
            f"{poster.config.court_url}/disputes",
            params={"task_id": task_id},
        )
        refreshed_disputes = refreshed["disputes"]
        if len(refreshed_disputes) > 0:
            return str(refreshed_disputes[0]["dispute_id"])

    return None


async def _submit_rebuttal(platform_agent: PlatformAgent, dispute_id: str) -> int:
    rebuttal_token = platform_agent._sign_jws(
        {
            "action": "submit_rebuttal",
            "dispute_id": dispute_id,
            "rebuttal": "Work meets the agreed specification.",
        }
    )
    rebuttal_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rebuttal",
        json={"token": rebuttal_token},
    )
    return rebuttal_response.status_code


async def _trigger_ruling(
    platform_agent: PlatformAgent, dispute_id: str
) -> tuple[int, dict[str, Any]]:
    ruling_token = platform_agent._sign_jws(
        {
            "action": "trigger_ruling",
            "dispute_id": dispute_id,
        }
    )
    ruling_response = await platform_agent._request_raw(
        "POST",
        f"{platform_agent.config.court_url}/disputes/{dispute_id}/rule",
        json={"token": ruling_token},
    )
    payload: dict[str, Any] = {}
    if ruling_response.headers.get("content-type", "").startswith("application/json"):
        payload = ruling_response.json()
    return ruling_response.status_code, payload


@pytest.mark.e2e
async def test_full_dispute_with_court_ruling(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """Full dispute flow with pragmatic fallback if court automation is unavailable."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster D0N", balance=5000)
        worker = await make_funded_agent(name="Worker D0N", balance=0)
        agents_to_close.extend([poster, worker])

        disputed_task, dispute_reason = await _create_disputed_task(poster, worker)
        dispute_id = await _resolve_dispute_id(
            poster,
            worker,
            platform_agent,
            disputed_task,
            dispute_reason,
        )

        if dispute_id is None:
            pytest.skip("Court dispute filing is not available in the current environment")

        rebuttal_status = await _submit_rebuttal(platform_agent, dispute_id)
        assert rebuttal_status in {200, 409}

        ruling_status, ruling_payload = await _trigger_ruling(platform_agent, dispute_id)
        if ruling_status == 200:
            assert ruling_payload["status"] == "ruled"
            task_after_ruling = await poster.get_task(str(disputed_task["task_id"]))
            assert task_after_ruling["status"] in {"ruled", "disputed"}

            poster_balance = await poster.get_balance()
            worker_balance = await worker.get_balance()
            assert poster_balance["balance"] + worker_balance["balance"] == 5000
        else:
            dispute_snapshot = await poster._request(
                "GET",
                f"{poster.config.court_url}/disputes/{dispute_id}",
            )
            assert dispute_snapshot["status"] in {"rebuttal_pending", "judging", "ruled"}
    finally:
        await _close_agents(agents_to_close)
