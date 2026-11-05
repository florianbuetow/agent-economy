"""Three concurrent tasks diverge without crossing money, events, or UI rows."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from types import ModuleType

    from base_agent.agent import BaseAgent
    from base_agent.config import AgentConfig
    from base_agent.platform import PlatformAgent

EXAMPLE_PATH = Path(__file__).resolve().parents[3] / "examples" / "external_agent.py"
LONG_WINDOWS = {
    "bidding_deadline_seconds": 3_600,
    "acceptance_window_seconds": 3_600,
    "execution_deadline_seconds": 7_200,
    "review_deadline_seconds": 3_600,
}
SHORT_WINDOWS = {
    "bidding_deadline_seconds": 1,
    "acceptance_window_seconds": 1,
    "execution_deadline_seconds": 30,
    "review_deadline_seconds": 30,
}


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adversarial_three_task_example", EXAMPLE_PATH)
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


def _version(task: dict[str, Any]) -> int:
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


async def _accept(
    poster: BaseAgent, worker: BaseAgent, task_id: str, amount: int
) -> dict[str, Any]:
    opened = await worker.get_task(task_id)
    bid = await worker.submit_bid(
        task_id=task_id,
        amount=amount,
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
    return accepted.body


async def _wait_task(agent: BaseAgent, task_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        task = await agent.get_task(task_id)
        if task["status"] == status:
            return task
        await asyncio.sleep(0.2)
    raise TimeoutError(f"task {task_id} did not reach {status}")


def _event_types(database_path: Path, task_id: str) -> list[str]:
    connection = sqlite3.connect(database_path)
    try:
        return [
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM events WHERE task_id=? ORDER BY event_id", (task_id,)
            ).fetchall()
        ]
    finally:
        connection.close()


@pytest.mark.e2e
async def test_three_tasks_approve_expire_and_rule_concurrently(  # noqa: PLR0915
    agent_config: AgentConfig,
    platform_agent: PlatformAgent,
    tmp_path: Path,
) -> None:
    """ECO-02: three independent escrows and lifecycle paths remain task-bound."""
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

    poster = build("three-task-poster")
    approved_worker = build("three-task-approved-worker")
    ruled_worker = build("three-task-ruled-worker")
    try:
        poster_id = await _onboard(poster)
        await _onboard(approved_worker)
        await _onboard(ruled_worker)
        reference = "three-task-campaign-funding"
        await platform_agent.credit_account(
            account_id=poster_id,
            amount=500,
            reference=reference,
            operation_id=operation_id_for("e2e-credit", (poster_id, reference)),
        )
        timing = example.Timing(poll_seconds=0.1, timeout_seconds=30.0)
        approved_id = await example.post_task(
            poster, "Approved task", "Return approved bytes.", 100, LONG_WINDOWS, timing
        )
        expired_id = await example.post_task(
            poster, "No bid task", "Receive no bids and expire.", 80, SHORT_WINDOWS, timing
        )
        ruled_id = await example.post_task(
            poster, "Ruled task", "Return correct ruled bytes.", 120, LONG_WINDOWS, timing
        )
        await _accept(poster, approved_worker, approved_id, 60)
        await _accept(poster, ruled_worker, ruled_id, 60)

        async def approve_flow() -> dict[str, Any]:
            accepted = await approved_worker.get_task(approved_id)
            await approved_worker.upload_asset(
                approved_id,
                "approved.txt",
                b"approved bytes\n",
                idempotency_key="three-task-approved-v1",
            )
            submitted = await approved_worker.submit_deliverable(
                approved_id,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(accepted),
            )
            assert submitted.status_code == 200
            submitted_task = await poster.get_task(approved_id)
            approved = await poster.approve_task(
                approved_id,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(submitted_task),
            )
            assert approved.status_code in {200, 202}
            return await _wait_task(poster, approved_id, "approved")

        async def expiry_flow() -> dict[str, Any]:
            await asyncio.sleep(1.1)
            return await _wait_task(poster, expired_id, "expired")

        async def ruling_flow() -> dict[str, Any]:
            accepted = await ruled_worker.get_task(ruled_id)
            await ruled_worker.upload_asset(
                ruled_id,
                "wrong.txt",
                b"wrong ruled bytes\n",
                idempotency_key="three-task-ruled-v1",
            )
            submitted = await ruled_worker.submit_deliverable(
                ruled_id,
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(accepted),
            )
            assert submitted.status_code == 200
            submitted_task = await poster.get_task(ruled_id)
            disputed = await poster.dispute_task(
                task_id=ruled_id,
                reason="Wrong bytes require the configured 50 percent mock ruling.",
                operation_id=f"op-{uuid.uuid4()}",
                expected_lifecycle_version=_version(submitted_task),
            )
            assert disputed.status_code in {200, 202}
            disputed_task = await _wait_task(poster, ruled_id, "disputed")
            await ruled_worker.submit_worker_rebuttal(
                ruled_id,
                disputed_task["dispute_id"],
                "The submitted bytes were wrong; apply the configured test ruling.",
            )
            return await _wait_task(poster, ruled_id, "ruled")

        approved, expired, ruled = await asyncio.gather(
            approve_flow(), expiry_flow(), ruling_flow()
        )
        assert approved["worker_payment"] == 60 and approved["poster_refund"] == 40
        assert expired["worker_payment"] == 0 and expired["poster_refund"] == 80
        assert ruled["worker_pct"] == 50
        assert ruled["worker_payment"] == 30 and ruled["poster_refund"] == 90
        poster_balance = int((await poster.get_balance())["balance"])
        approved_balance = int((await approved_worker.get_balance())["balance"])
        ruled_balance = int((await ruled_worker.get_balance())["balance"])
        assert (poster_balance, approved_balance, ruled_balance) == (410, 60, 30)
        assert poster_balance + approved_balance + ruled_balance == 500

        async with httpx.AsyncClient(base_url=urls["task_board"]) as raw:
            listing = (await raw.get("/tasks", params={"poster_id": poster_id})).json()["tasks"]
        status_by_id = {item["task_id"]: item["status"] for item in listing}
        assert status_by_id[approved_id] == "approved"
        assert status_by_id[expired_id] == "expired"
        assert status_by_id[ruled_id] == "ruled"
        database_path = Path(os.environ["ISOLATED_RUNTIME_DIR"]) / "data/economy.db"
        assert "task.approved" in _event_types(database_path, approved_id)
        assert "task.expired" in _event_types(database_path, expired_id)
        assert "task.ruled" in _event_types(database_path, ruled_id)
    finally:
        await ruled_worker.close()
        await approved_worker.close()
        await poster.close()
