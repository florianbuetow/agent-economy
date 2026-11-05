"""One worker resumes overlapping assigned jobs without cross-task leakage."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from math_worker.history import TaskOutcome
from math_worker.loop import MathWorkerLoop
from tests.unit.math_worker.runtime_fixtures import (
    POSTER_ID,
    WORKER_ID,
    install_successful_native_transports,
    runtime_worker,
)
from tests.unit.math_worker.test_worker_job_resume import _config, _llm

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

TASK_A = "t-10000000-0000-4000-8000-000000000001"
TASK_B = "t-10000000-0000-4000-8000-000000000002"


def _task(task_id: str, status: str, version: int) -> dict[str, object]:
    return {
        "task_id": task_id,
        "title": f"Solve {task_id[-1]}",
        "spec": f"Return the exact answer for {task_id}",
        "reward": 50,
        "status": status,
        "lifecycle_version": version,
        "poster_id": POSTER_ID,
        "worker_id": WORKER_ID,
        "escrow_id": f"esc-{task_id}",
        "accepted_reward": 50,
        "accepted_bid_amount": 40,
    }


class _MultiBoard:
    def __init__(self) -> None:
        self.tasks = {
            TASK_A: _task(TASK_A, "accepted", 8),
            TASK_B: _task(TASK_B, "accepted", 8),
        }
        self.assets: dict[str, list[dict[str, object]]] = {TASK_A: [], TASK_B: []}

    async def get_task(self, task_id: str) -> dict[str, object]:
        return dict(self.tasks[task_id])

    async def list_tasks(
        self, status: str | None = None, worker_id: str | None = None
    ) -> list[dict[str, object]]:
        return [
            dict(task)
            for task in self.tasks.values()
            if (status is None or task["status"] == status)
            and (worker_id is None or task["worker_id"] == worker_id)
        ]

    async def upload_asset(self, task_id: str, filename: str, content: bytes) -> dict[str, object]:
        asset = {
            "asset_id": f"asset-{task_id[-1]}-{len(self.assets[task_id]) + 1}",
            "uploader_id": WORKER_ID,
            "filename": filename,
            "content_type": "text/plain",
            "size_bytes": len(content),
            "content_hash": hashlib.sha256(content).hexdigest(),
            "uploaded_at": "2026-11-05T00:00:00Z",
        }
        self.assets[task_id].append(asset)
        return {"task_id": task_id, **asset}

    async def list_assets(self, task_id: str) -> list[dict[str, object]]:
        return [dict(asset) for asset in self.assets[task_id]]


def _wire(agent: BaseAgent, board: _MultiBoard) -> Any:
    mocked = cast("Any", agent)
    mocked.get_task = AsyncMock(side_effect=board.get_task)
    mocked.list_tasks = AsyncMock(side_effect=board.list_tasks)
    mocked.upload_asset = AsyncMock(side_effect=board.upload_asset)
    mocked.list_assets = AsyncMock(side_effect=board.list_assets)
    mocked.get_balance = AsyncMock(return_value={"balance": 100})
    return mocked


async def test_crash_in_one_of_two_jobs_never_reuploads_or_cross_wires_after_restart(
    tmp_path: Path,
) -> None:
    """ECO-03: each assigned job resumes from its own journal and public state."""
    board = _MultiBoard()
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        crashed = False

        async def crash_a_after_upload(task_id: str) -> dict[str, object]:
            nonlocal crashed
            if task_id == TASK_A and board.assets[TASK_A] and not crashed:
                crashed = True
                raise RuntimeError("process died after task A upload")
            return await board.get_task(task_id)

        mocked.get_task = AsyncMock(side_effect=crash_a_after_upload)
        loop = MathWorkerLoop(agent, _llm("ANSWER: one", "ANSWER: two"), _config(), commands)
        await loop._cycle()

    assert crashed
    assert len(board.assets[TASK_A]) == len(board.assets[TASK_B]) == 1
    assert board.assets[TASK_A][0]["content_hash"] == hashlib.sha256(b"one").hexdigest()
    assert board.assets[TASK_B][0]["content_hash"] == hashlib.sha256(b"two").hexdigest()
    assert [call.args[0] for call in mocked.submit_deliverable.await_args_list] == [TASK_B]

    board.tasks[TASK_A] = _task(TASK_A, "expired", 9)
    board.tasks[TASK_B] = _task(TASK_B, "submitted", 9)
    async with runtime_worker(root) as (agent, store, commands):
        restarted = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()

    restarted.upload_asset.assert_not_awaited()
    restarted.submit_deliverable.assert_not_awaited()
    assert len(board.assets[TASK_A]) == len(board.assets[TASK_B]) == 1
    outcomes = {record.task_id: record.outcome for record in loop._history.records}
    assert outcomes[TASK_A] == TaskOutcome.TIMEOUT
    assert TASK_B not in outcomes
