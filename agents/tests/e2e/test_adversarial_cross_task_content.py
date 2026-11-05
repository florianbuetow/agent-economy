"""Cross-task asset binding and semantic-review separation."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from types import ModuleType

    from base_agent.agent import BaseAgent
    from base_agent.config import AgentConfig
    from base_agent.platform import PlatformAgent

EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "examples" / "external_agent.py"
WINDOWS = {
    "bidding_deadline_seconds": 3_600,
    "acceptance_window_seconds": 3_600,
    "execution_deadline_seconds": 7_200,
    "review_deadline_seconds": 3_600,
}


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adversarial_cross_task_example", EXAMPLE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _onboard(agent: BaseAgent) -> str:
    agent_id = (await agent.register())["agent_id"]
    try:
        await agent.create_account()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 409:
            raise
    return agent_id


def _version(task: dict[str, object]) -> int:
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


async def _accept_worker(poster: BaseAgent, worker: BaseAgent, task_id: str) -> None:
    opened = await worker.get_task(task_id)
    bid = await worker.submit_bid(
        task_id=task_id,
        amount=60,
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=_version(opened),
    )
    assert bid.status_code == 201
    accepted = await poster.accept_bid(
        task_id=task_id,
        bid_id=bid.body["bid_id"],
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=_version(opened),
    )
    assert accepted.status_code == 200


@pytest.mark.e2e
async def test_cross_task_asset_id_is_rejected_but_wrong_fresh_content_needs_review(
    agent_config: AgentConfig,
    platform_agent: PlatformAgent,
    tmp_path: Path,
) -> None:
    """AST-01: binding is mechanical; semantic correctness is poster/Court review."""
    example = _load_example()
    urls = {
        "identity": agent_config.identity_url,
        "bank": agent_config.bank_url,
        "task_board": agent_config.task_board_url,
        "reputation": agent_config.reputation_url,
        "court": agent_config.court_url,
    }
    policy = agent_config.token_policy

    def build(name: str) -> BaseAgent:
        return example.build_agent(
            name,
            tmp_path / "keys",
            urls,
            agent_config.token_ttl_seconds,
            policy.max_lifetime_seconds,
            policy.clock_skew_seconds,
        )

    poster = build("cross-task-poster")
    worker = build("cross-task-worker")
    try:
        poster_id = await _onboard(poster)
        worker_id = await _onboard(worker)
        reference = "cross-task-campaign-funding"
        await platform_agent.credit_account(
            account_id=poster_id,
            amount=500,
            reference=reference,
            operation_id=operation_id_for("e2e-credit", (poster_id, reference)),
        )
        timing = example.Timing(poll_seconds=0.1, timeout_seconds=30.0)
        task_a = await example.post_task(
            poster, "Task A", "Return bytes for A.", 100, WINDOWS, timing
        )
        task_b = await example.post_task(
            poster, "Task B", "Return different bytes for B.", 100, WINDOWS, timing
        )
        await _accept_worker(poster, worker, task_a)
        await _accept_worker(poster, worker, task_b)

        asset_a = await worker.upload_asset(
            task_a,
            "answer-a.txt",
            b"correct answer for A\n",
            idempotency_key="cross-task-a-v1",
        )
        with pytest.raises(httpx.HTTPStatusError) as cross_task:
            await worker.download_asset(task_b, asset_a["asset_id"])
        assert cross_task.value.response.status_code == 404

        wrong_b = await worker.upload_asset(
            task_b,
            "answer-b.txt",
            b"correct answer for A\n",
            idempotency_key="cross-task-b-wrong-v1",
        )
        accepted_b = await worker.get_task(task_b)
        submitted = await worker.submit_deliverable(
            task_b,
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=_version(accepted_b),
        )
        assert submitted.status_code == 200
        observed = await poster.download_asset(task_b, wrong_b["asset_id"])
        assert observed == b"correct answer for A\n"
        assert observed != b"correct answer for B\n"

        submitted_task = await poster.get_task(task_b)
        disputed = await poster.dispute_task(
            task_id=task_b,
            reason="Delivered Task A content does not satisfy Task B.",
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=_version(submitted_task),
        )
        assert disputed.status_code in {200, 202}
        final = await poster.get_task(task_b)
        assert final["status"] == "disputed"
        assert final["worker_id"] == worker_id
        assert final["dispute_reason"] == "Delivered Task A content does not satisfy Task B."
    finally:
        await worker.close()
        await poster.close()
