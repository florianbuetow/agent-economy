"""Real process death with two accepted jobs preserves task-local recovery."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from service_auth.factory import AgentFactory
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from base_agent.agent import BaseAgent

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ACTOR = Path(__file__).with_name("multi_job_crash_actor.py")
TASK_A = "t-10000000-0000-4000-8000-000000000001"
TASK_B = "t-10000000-0000-4000-8000-000000000002"


def _intent(poster_id: str, task_id: str, execution_seconds: int) -> TaskPostingIntent:
    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": poster_id,
            "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
            "title": f"Multi-job {task_id[-1]}",
            "spec": f"Return isolated output for {task_id}.",
            "reward": 100,
            "bidding_deadline_seconds": 3_600,
            "acceptance_window_seconds": 3_600,
            "execution_deadline_seconds": execution_seconds,
            "review_deadline_seconds": 3_600,
        }
    )


def _version(task: dict[str, Any]) -> int:
    value = task["lifecycle_version"]
    assert type(value) is int
    return value


async def _accept(poster: BaseAgent, worker: BaseAgent, task_id: str) -> None:
    opened = await worker.get_task(task_id)
    bid = await worker.submit_bid(
        task_id,
        60,
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=_version(opened),
    )
    accepted = await poster.accept_bid(
        task_id,
        bid.body["bid_id"],
        operation_id=f"op-{uuid.uuid4()}",
        expected_lifecycle_version=_version(opened),
    )
    assert accepted.status_code == 200


async def _wait_marker(path: Path) -> None:
    for _attempt in range(3_000):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise TimeoutError("worker never reached the post-upload crash gate")


async def _wait_status(agent: BaseAgent, task_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        task = await agent.get_task(task_id)
        if task["status"] == status:
            return task
        await asyncio.sleep(0.1)
    raise TimeoutError(f"task {task_id} did not reach {status}")


async def _actor(config: Path, *, crash_task: str | None, marker: Path) -> Process:
    args = [sys.executable, str(ACTOR), "--config", str(config)]
    if crash_task is not None:
        args.extend(("--crash-task", crash_task, "--marker", str(marker)))
    return await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )


async def _cleanup(processes: list[Process]) -> None:
    for process in processes:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            await process.communicate()


@pytest.mark.e2e
async def test_process_crash_recovers_uploaded_job_while_other_job_expires(tmp_path: Path) -> None:
    """ECO-03: SIGKILL recovery adopts A's upload; unfinished B alone expires."""
    config_path = Path(os.environ["AGENT_CONFIG_PATH"])
    factory = AgentFactory(config_path=config_path)
    poster = factory.create_agent("operator")
    worker = factory.create_agent("alice")
    children: list[Process] = []
    try:
        poster_id = (await poster.register())["agent_id"]
        worker_id = (await worker.register())["agent_id"]
        try:
            await worker.create_account()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 409:
                raise
        poster_initial = int((await poster.get_balance())["balance"])
        for task_id, seconds in ((TASK_A, 3_600), (TASK_B, 1)):
            posted = await poster.submit_task_posting(_intent(poster_id, task_id, seconds))
            assert posted.status_code == 201
            await _accept(poster, worker, task_id)

        marker = tmp_path / "uploaded.marker"
        crashed = await _actor(config_path, crash_task=TASK_A, marker=marker)
        children.append(crashed)
        await _wait_marker(marker)
        os.killpg(crashed.pid, signal.SIGKILL)
        await crashed.wait()
        assert crashed.returncode == -signal.SIGKILL
        await asyncio.sleep(1.1)
        expired = await _wait_status(poster, TASK_B, "expired")
        assert expired["poster_refund"] == 100

        restarted = await _actor(config_path, crash_task=None, marker=marker)
        children.append(restarted)
        stdout, stderr = await asyncio.wait_for(restarted.communicate(), timeout=30)
        assert restarted.returncode == 0, (stdout + stderr).decode()
        submitted = await _wait_status(poster, TASK_A, "submitted")
        assert submitted["worker_id"] == worker_id
        assets_a = await worker.list_assets(TASK_A)
        assets_b = await worker.list_assets(TASK_B)
        assert len(assets_a) == 1
        assert assets_a[0]["asset_id"] == marker.read_text(encoding="utf-8")
        assert assets_b == []
        assert int((await poster.get_balance())["balance"]) == poster_initial - 100
        assert int((await worker.get_balance())["balance"]) == 0
    finally:
        await _cleanup(children)
        await worker.close()
        await poster.close()
