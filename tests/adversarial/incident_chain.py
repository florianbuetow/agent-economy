"""Disposable end-to-end replay of the 2026-11-04 unsuitable-worker incident."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from scripts.isolated_test_runtime import IsolatedRuntime
from service_auth.errors import EconomyApiError
from service_auth.financial_identity import operation_id_for

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig
from base_agent.factory import AgentFactory
from base_agent.signing import TokenPolicy
from math_worker.parser import parse_solution


def _operation_id() -> str:
    return f"op-{uuid.uuid4()}"


def _configure_three_zero_judges(config_path: Path) -> None:
    config = yaml.safe_load(config_path.read_text())
    config["judges"]["panel_size"] = 3
    config["judges"]["mock_worker_pct"] = 0
    config["judges"]["judges"] = [
        {"id": f"judge-{index}", "provider": "mock", "model": "campaign-mock"} for index in range(3)
    ]
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))


def _agent_config(runtime: IsolatedRuntime, name: str) -> AgentConfig:
    key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name=name,
        private_key=key,
        public_key=key.public_key(),
        identity_url=runtime.service_url("identity"),
        bank_url=runtime.service_url("central-bank"),
        task_board_url=runtime.service_url("task-board"),
        reputation_url=runtime.service_url("reputation"),
        court_url=runtime.service_url("court"),
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


async def _new_agent(
    runtime: IsolatedRuntime,
    platform,
    name: str,
    balance: int,
) -> BaseAgent:
    agent = BaseAgent(_agent_config(runtime, name))
    await agent.register()
    assert agent.agent_id is not None
    await platform.create_account(
        agent.agent_id,
        balance,
        operation_id=(
            operation_id_for("campaign-account", (agent.agent_id,)) if balance > 0 else None
        ),
    )
    return agent


async def _submit_invalid_work(poster: BaseAgent, worker: BaseAgent) -> tuple[str, int]:
    intent = poster.prepare_task_posting(
        title="Invert a binary tree",
        spec="Give example Python code that inverts a binary tree.",
        reward=100,
        bidding_deadline_seconds=60,
        acceptance_window_seconds=30,
        execution_deadline_seconds=120,
        review_deadline_seconds=90,
    )
    published = await poster.submit_task_posting(intent)
    assert published.status_code == 201
    task = await poster.get_task(intent.task_id)
    invalid_output = parse_solution("I cannot provide code.\nANSWER: 7")
    assert invalid_output == "7"
    bid = await worker.submit_bid(
        task_id=intent.task_id,
        amount=1,
        operation_id=_operation_id(),
        expected_lifecycle_version=int(task["lifecycle_version"]),
    )
    accepted = await poster.accept_bid(
        task_id=intent.task_id,
        bid_id=str(bid.body["bid_id"]),
        operation_id=_operation_id(),
        expected_lifecycle_version=int(task["lifecycle_version"]),
    )
    await worker.upload_asset(intent.task_id, "answer.txt", invalid_output.encode())
    submitted = await worker.submit_deliverable(
        intent.task_id,
        operation_id=_operation_id(),
        expected_lifecycle_version=int(accepted.body["lifecycle_version"]),
    )
    return intent.task_id, int(submitted.body["lifecycle_version"])


async def _recover_dispute(
    runtime: IsolatedRuntime,
    poster: BaseAgent,
    task_id: str,
    submitted_version: int,
) -> tuple[str, str]:
    runtime.stop_service("court")
    operation_id = _operation_id()
    reason = "The worker submitted unrelated output instead of Python code."
    try:
        await poster.dispute_task(
            task_id,
            reason,
            operation_id=operation_id,
            expected_lifecycle_version=submitted_version,
        )
    except EconomyApiError as exc:
        assert (exc.status_code, exc.error) == (502, "court_unavailable")
        observation = exc.error
    except httpx.ReadTimeout:
        observation = "unknown_outcome_timeout"
    else:
        raise AssertionError("Court outage must leave a typed pending dispute operation")
    pending = await poster.get_task(task_id)
    assert pending["status"] == "submitted"
    assert pending["pending_transition"]["operation_id"] == operation_id
    assert pending["pending_transition"]["effect_state"] == "pending"
    runtime.start_service("court")
    replay = await poster.dispute_task(
        task_id,
        reason,
        operation_id=operation_id,
        expected_lifecycle_version=submitted_version,
    )
    assert replay.status_code == 200
    disputes = await poster.list_disputes(task_id=task_id)
    assert len(disputes) == 1
    return str(disputes[0]["dispute_id"]), observation


async def _verify_ruling(
    runtime: IsolatedRuntime,
    poster: BaseAgent,
    worker: BaseAgent,
    task_id: str,
    dispute_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    async with httpx.AsyncClient(base_url=runtime.service_url("ui")) as client:
        before_rebuttal = (await client.get(f"/api/tasks/{task_id}")).json()
    assert before_rebuttal["status"] == "disputed"
    assert before_rebuttal["dispute"]["rebuttal"] is None
    assert isinstance(before_rebuttal["dispute"]["rebuttal_deadline"], str)
    await worker.submit_worker_rebuttal(
        task_id,
        dispute_id,
        "The complaint is valid; the frozen output did not provide the requested Python example.",
    )
    ruled = await poster.get_task(task_id)
    assert (ruled["status"], ruled["worker_pct"]) == ("ruled", 0)
    assert (ruled["worker_payment"], ruled["poster_refund"]) == (0, 100)
    async with httpx.AsyncClient(base_url=runtime.service_url("court")) as client:
        response = await client.get(f"/disputes/{dispute_id}")
        response.raise_for_status()
        court_dispute = response.json()
    assert len(court_dispute["votes"]) == 3
    assert {vote["worker_pct"] for vote in court_dispute["votes"]} == {0}
    assets = await poster.list_assets(task_id)
    assert len(assets) == 1
    assert await poster.download_asset(task_id, str(assets[0]["asset_id"])) == b"7"
    async with httpx.AsyncClient(base_url=runtime.service_url("ui")) as client:
        final_ui = (await client.get(f"/api/tasks/{task_id}")).json()
        events = (await client.get(f"/api/events?limit=100&task_id={task_id}")).json()
    assert (final_ui["status"], final_ui["worker_payment"], final_ui["poster_refund"]) == (
        "ruled",
        0,
        100,
    )
    event_types = [event["event_type"] for event in events["events"]]
    assert (event_types.count("task.disputed"), event_types.count("task.ruled")) == (1, 1)
    return ruled, court_dispute


async def _run(runtime: IsolatedRuntime) -> dict[str, Any]:
    platform = AgentFactory(config_path=runtime.agent_config_path).platform_agent()
    poster: BaseAgent | None = None
    worker: BaseAgent | None = None
    await platform.register()
    try:
        poster = await _new_agent(runtime, platform, "Campaign Poster", 1000)
        worker = await _new_agent(runtime, platform, "Campaign Math Worker", 0)
        assert poster.agent_id is not None
        assert worker.agent_id is not None

        task_id, submitted_version = await _submit_invalid_work(poster, worker)
        dispute_id, outage_observation = await _recover_dispute(
            runtime, poster, task_id, submitted_version
        )
        ruled, court_dispute = await _verify_ruling(runtime, poster, worker, task_id, dispute_id)
        assert (await poster.get_balance())["balance"] == 1000
        assert (await worker.get_balance())["balance"] == 0

        return {
            "task_id": task_id,
            "poster_id": poster.agent_id,
            "worker_id": worker.agent_id,
            "dispute_id": dispute_id,
            "invalid_output_bytes": 1,
            "outage_observation": outage_observation,
            "claim_count": 1,
            "judge_votes": len(court_dispute["votes"]),
            "worker_pct": ruled["worker_pct"],
            "worker_payment": ruled["worker_payment"],
            "poster_refund": ruled["poster_refund"],
            "poster_balance": (await poster.get_balance())["balance"],
            "worker_balance": (await worker.get_balance())["balance"],
        }
    finally:
        if poster is not None:
            await poster.close()
        if worker is not None:
            await worker.close()
        await platform.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    runtime = IsolatedRuntime.create(
        repo_root=Path(__file__).resolve().parents[2],
        artifacts_root=args.artifacts_root,
    )
    _configure_three_zero_judges(runtime.config_paths["court"])
    try:
        runtime.start()
        assert runtime.provision() == 0
        result = asyncio.run(_run(runtime))
        args.result.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        runtime.stop()


if __name__ == "__main__":
    raise SystemExit(main())
