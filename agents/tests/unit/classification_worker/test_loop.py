"""ClassificationWorkerLoop end to end against a fake public Task Board and a real journal."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from classification_worker.adaptation import PricingRule
from classification_worker.loop import ClassificationWorkerLoop, LoopSettings
from classification_worker.solvers import KeywordSolver
from task_feeder.workloads.classification import ClassificationTask, render_spec
from tests.unit.math_worker.runtime_fixtures import (
    SERVICE_URLS,
    TASK_ID,
    WORKER_ID,
    install_successful_native_transports,
    runtime_worker,
    task_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

SPEC = render_spec(
    ClassificationTask(
        title="Sentiment",
        task_type="sentiment",
        level=1,
        instructions="Label sentiment.",
        labels=("positive", "negative"),
        inputs=(("s1", "love it"), ("s2", "terrible")),
        expected={"s1": "positive", "s2": "negative"},
        min_correct=2,
        rationale_max_chars=200,
    )
)
RULE = PricingRule(
    initial_bid_pct=80,
    min_bid_pct=50,
    max_bid_pct=100,
    raise_step_pct=10,
    lower_step_pct=10,
    min_samples=2,
    min_approval_pct=50,
)


def _settings() -> LoopSettings:
    return LoopSettings(
        handle="classbot_keyword",
        scan_interval_seconds=0,
        poll_interval_seconds=0,
        max_poll_attempts=1,
        error_backoff_seconds=0,
        min_reward=1,
        max_reward=1000,
        pricing=RULE,
    )


class _Board:
    def __init__(self) -> None:
        self.task: dict[str, object] = {}
        self.set("open", 7)
        self.assets: list[dict[str, object]] = []

    def set(self, status: str, version: int, **extra: object) -> None:
        self.task = task_snapshot(status, version)
        self.task.update(spec=SPEC, title="Sentiment", **extra)

    async def get_task(self, task_id: str) -> dict[str, object]:
        assert task_id == TASK_ID
        return dict(self.task)

    async def list_tasks(
        self, status: str | None = None, worker_id: str | None = None
    ) -> list[dict[str, object]]:
        if status is not None and self.task["status"] != status:
            return []
        if worker_id is not None and self.task["worker_id"] != worker_id:
            return []
        return [dict(self.task)]

    async def upload_asset(self, task_id: str, filename: str, content: bytes) -> dict[str, object]:
        asset = {
            "asset_id": f"asset-{len(self.assets) + 1}",
            "uploader_id": WORKER_ID,
            "filename": filename,
            "content_hash": hashlib.sha256(content).hexdigest(),
            "content": content,
        }
        self.assets.append(asset)
        return {"task_id": task_id, "asset_id": asset["asset_id"]}

    async def signed_request(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> dict[str, object]:
        assert method == "GET"
        assert url == f"{SERVICE_URLS['task_board_url']}/tasks/{TASK_ID}/assets"
        assert headers["Authorization"].startswith("Bearer ")
        return {"assets": [{k: v for k, v in a.items() if k != "content"} for a in self.assets]}


def _wire(agent: BaseAgent, board: _Board) -> Any:
    mocked = cast("Any", agent)
    mocked.get_task = AsyncMock(side_effect=board.get_task)
    mocked.list_tasks = AsyncMock(side_effect=board.list_tasks)
    mocked.upload_asset = AsyncMock(side_effect=board.upload_asset)
    mocked._request = AsyncMock(side_effect=board.signed_request)
    mocked.get_balance = AsyncMock(return_value={"balance": 100})
    mocked.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})
    mocked.list_disputes = AsyncMock(return_value=[{"dispute_id": "disp-1"}])
    return mocked


def _loop(agent: BaseAgent, commands: Any) -> ClassificationWorkerLoop:
    solver = KeywordSolver({"positive": ["love"], "negative": ["terrible"]})
    return ClassificationWorkerLoop(agent, solver, _settings(), commands)


async def test_full_cycle_bids_by_policy_uploads_two_outputs_and_records_typed_outcome(
    tmp_path: Path,
) -> None:
    board = _Board()
    async with runtime_worker(tmp_path / "rt") as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = _loop(agent, commands)

        await loop._cycle()  # scan + bid at 80% of reward 50
        assert mocked.submit_bid.await_args.args == (TASK_ID, 40)

        board.set("accepted", 8)
        await loop._cycle()  # solve, upload labels.json + rationale.txt, submit
        names = [a["filename"] for a in board.assets]
        assert names == ["labels.json", "rationale.txt"]
        labels = json.loads(cast("bytes", board.assets[0]["content"]))
        assert labels == {"s1": "positive", "s2": "negative"}
        assert mocked.submit_deliverable.await_count == 1

        board.set("approved", 10)
        await loop._cycle()
        job = commands.jobs.load(TASK_ID)
        assert job is not None
        assert job["outcome"] == "approved"
        assert job["task_type"] == "sentiment"
        assert job["outcome_seq"] == 1
        # the recorded approval raised the next price for this task type
        assert loop.policy_state("sentiment").bid_amount(reward=50) == 45


async def test_upload_is_not_repeated_after_restart(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    root = tmp_path / "rt"
    async with runtime_worker(root) as (agent, store, commands):
        _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = _loop(agent, commands)
        job: dict[str, Any] = {"phase": "accepted"}
        answer = await loop._phase_solving(dict(board.task))
        assert answer is not None
        await loop._upload_once(TASK_ID, answer, job)
        await loop._upload_once(TASK_ID, answer, job)
    assert len(board.assets) == 2


async def test_adaptation_persists_across_restarts_and_stops_bidding(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    async with runtime_worker(root) as (_agent, _store, commands):
        for index in (1, 2):
            commands.jobs.save(
                f"t-10000000-0000-4000-8000-00000000010{index}",
                {
                    "outcome": "disputed_lost",
                    "task_type": "sentiment",
                    "outcome_seq": index,
                    "bid_amount": 40,
                    "payout": 0,
                },
            )
    board = _Board()
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        mocked.list_tasks = AsyncMock(
            side_effect=lambda status=None, worker_id=None: (
                [dict(board.task)] if status == "open" and worker_id is None else []
            )
        )
        loop = _loop(agent, commands)
        assert not loop.policy_state("sentiment").bidding
        assert await loop._phase_scanning() is None
        mocked.submit_bid.assert_not_awaited()


async def test_math_tasks_are_ignored_by_the_scanner(tmp_path: Path) -> None:
    board = _Board()
    board.task["spec"] = "TASK: Calculate the sum of 10, 2."
    async with runtime_worker(tmp_path / "rt") as (agent, _store, commands):
        _wire(agent, board)
        loop = _loop(agent, commands)
        assert await loop._phase_scanning() is None


async def test_rebuttal_cites_the_published_rule_without_an_llm(tmp_path: Path) -> None:
    board = _Board()
    async with runtime_worker(tmp_path / "rt") as (agent, _store, commands):
        _wire(agent, board)
        loop = _loop(agent, commands)
        answer = await loop._phase_solving(dict(board.task))
        assert answer is not None
        text = await loop._compose_rebuttal(dict(board.task), answer, "2 of 2 wrong")
        assert "labels.json" in text
        assert "ACCEPTANCE" in text or "at least" in text
