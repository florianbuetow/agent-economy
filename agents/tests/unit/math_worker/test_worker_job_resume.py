"""Plan item 0.5: the worker resumes assigned jobs across restarts and slow acceptance."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_worker.config import MathWorkerConfig
from math_worker.history import TaskOutcome
from math_worker.loop import MathWorkerLoop
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

_FILENAME = f"{TASK_ID}_solution.txt"


def _config() -> MathWorkerConfig:
    return MathWorkerConfig(
        handle="mathbot",
        scan_interval_seconds=0,
        poll_interval_seconds=0,
        max_poll_attempts=1,
        error_backoff_seconds=0,
        min_reward=1,
        max_reward=1000,
    )


class _Board:
    """Mutable public Task Board view shared by every worker incarnation."""

    def __init__(self) -> None:
        self.task: dict[str, object] = task_snapshot("open", 7)
        self.assets: list[dict[str, object]] = []
        self.signed_list_calls = 0

    def set(self, status: str, version: int, **extra: object) -> None:
        self.task = task_snapshot(status, version)
        self.task.update(extra)

    async def get_task(self, task_id: str) -> dict[str, object]:
        assert task_id == TASK_ID
        return dict(self.task)

    async def list_tasks(
        self, status: str | None = None, worker_id: str | None = None
    ) -> list[dict[str, object]]:
        task = self.task
        if status is not None and task["status"] != status:
            return []
        if worker_id is not None and task["worker_id"] != worker_id:
            return []
        return [dict(task)]

    async def upload_asset(self, task_id: str, filename: str, content: bytes) -> dict[str, object]:
        asset = {
            "asset_id": f"asset-{len(self.assets) + 1}",
            "uploader_id": WORKER_ID,
            "filename": filename,
            "content_type": "text/plain",
            "size_bytes": len(content),
            "content_hash": hashlib.sha256(content).hexdigest(),
            "uploaded_at": "2026-10-30T12:00:00Z",
        }
        self.assets.append(asset)
        return {"task_id": task_id, **asset}

    async def list_assets(self, task_id: str) -> list[dict[str, object]]:
        """Serve the asset list the SDK fetches with its signed read."""
        assert task_id == TASK_ID
        self.signed_list_calls += 1
        return [dict(asset) for asset in self.assets]

    async def signed_request(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> dict[str, object]:
        """Serve only a signed asset list; Task Board rejects unsigned asset reads."""
        assert method == "GET"
        assert url == f"{SERVICE_URLS['task_board_url']}/tasks/{TASK_ID}/assets"
        assert headers["Authorization"].startswith("Bearer ")
        self.signed_list_calls += 1
        return {"task_id": TASK_ID, "assets": [dict(asset) for asset in self.assets]}


def _wire(agent: BaseAgent, board: _Board) -> Any:
    mocked = cast("Any", agent)
    mocked.get_task = AsyncMock(side_effect=board.get_task)
    mocked.list_tasks = AsyncMock(side_effect=board.list_tasks)
    mocked.upload_asset = AsyncMock(side_effect=board.upload_asset)
    mocked.list_assets = AsyncMock(side_effect=board.list_assets)
    mocked.get_balance = AsyncMock(return_value={"balance": 100})
    mocked.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})
    mocked.list_disputes = AsyncMock(return_value=[{"dispute_id": "disp-1"}])
    return mocked


def _llm(*contents: str) -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=[MagicMock(content=content) for content in contents])
    return llm


async def _bid_and_stop(root: Path, board: _Board) -> None:
    """First incarnation: select, bid, and see no acceptance within the poll budget."""
    async with runtime_worker(root) as (agent, store, commands):
        _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(TASK_ID, "40"), _config(), commands)
        await loop._cycle()
        assert loop._history.records == []


async def test_late_acceptance_after_poll_budget_is_still_executed(tmp_path: Path) -> None:
    board = _Board()
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(TASK_ID, "40", "ANSWER: 42"), _config(), commands)

        await loop._cycle()  # bid placed, poll budget exhausted while open
        await loop._cycle()  # still open: keep waiting, never re-bid or abandon
        assert loop._history.records == []
        assert mocked.submit_bid.await_count == 1

        board.set("accepted", 8)
        await loop._cycle()
        board.set("approved", 10)
        await loop._cycle()

    mocked.upload_asset.assert_awaited_once_with(TASK_ID, _FILENAME, b"42")
    assert mocked.submit_deliverable.await_count == 1
    assert [r.outcome for r in loop._history.records] == [TaskOutcome.APPROVED]
    assert loop._history.records[0].payout == 50
    assert loop._history.records[0].bid_amount == 40


async def test_restart_after_acceptance_submits_once_and_records_payment(tmp_path: Path) -> None:
    board = _Board()
    root = tmp_path / "runtime"
    await _bid_and_stop(root, board)
    board.set("accepted", 8)

    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42"), _config(), commands)
        await loop._cycle()
        board.set("approved", 10)
        await loop._cycle()
        job = commands.jobs.load(TASK_ID)

    mocked.upload_asset.assert_awaited_once_with(TASK_ID, _FILENAME, b"42")
    assert mocked.submit_deliverable.await_count == 1
    mocked.submit_bid.assert_not_awaited()
    assert [r.outcome for r in loop._history.records] == [TaskOutcome.APPROVED]
    assert loop._history.records[0].payout == 50
    assert job is not None
    assert job["outcome"] == "approved"
    assert job["payout"] == 50


async def test_accepted_job_without_local_record_is_discovered_and_executed(
    tmp_path: Path,
) -> None:
    board = _Board()
    board.set("accepted", 8)
    async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42"), _config(), commands)
        await loop._cycle()

    mocked.list_tasks.assert_any_await(status="accepted", worker_id=WORKER_ID)
    assert mocked.submit_deliverable.await_count == 1


async def test_crash_between_upload_and_submit_does_not_reupload(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        calls = {"n": 0}

        async def crash_after_upload(task_id: str) -> dict[str, object]:
            calls["n"] += 1
            if mocked.upload_asset.await_count == 1:
                raise RuntimeError("process died after upload")
            return await board.get_task(task_id)

        mocked.get_task = AsyncMock(side_effect=crash_after_upload)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42"), _config(), commands)
        await loop._cycle()  # the failure is logged; the job stays journaled
        mocked.submit_deliverable.assert_not_awaited()

    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()

    mocked.upload_asset.assert_not_awaited()
    assert len(board.assets) == 1
    assert mocked.submit_deliverable.await_count == 1


async def test_upload_started_without_asset_id_adopts_existing_asset(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)

        async def upload_then_die(task_id: str, filename: str, content: bytes) -> object:
            await board.upload_asset(task_id, filename, content)
            raise RuntimeError("process died before the upload response was journaled")

        mocked.upload_asset = AsyncMock(side_effect=upload_then_die)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42"), _config(), commands)
        await loop._cycle()  # the failure is logged; the job stays journaled

    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()
        job = commands.jobs.load(TASK_ID)

    mocked.upload_asset.assert_not_awaited()
    assert len(board.assets) == 1
    assert mocked.submit_deliverable.await_count == 1
    assert job is not None
    assert job["upload"]["asset_id"] == "asset-1"
    assert board.signed_list_calls == 1


async def test_solve_failure_retries_next_cycle(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm("   ", "ANSWER: 42"), _config(), commands)

        await loop._cycle()
        mocked.submit_deliverable.assert_not_awaited()
        job = commands.jobs.load(TASK_ID)
        assert job is not None
        assert job["last_error"]
        assert job["outcome"] is None

        await loop._cycle()

    assert mocked.submit_deliverable.await_count == 1
    assert loop._history.records == []


async def test_restart_after_dispute_files_exactly_one_rebuttal(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42", "My answer is right."), _config(), commands)
        await loop._cycle()
        board.set("disputed", 10, dispute_id="disp-1", dispute_reason="Wrong answer")
        await loop._cycle()  # files the rebuttal; ruling not yet in
        mocked.submit_worker_rebuttal.assert_awaited_once_with(
            TASK_ID, "disp-1", "My answer is right."
        )

    # The Board has not projected the rebuttal timestamp yet: only the journal knows.
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()
        mocked.submit_worker_rebuttal.assert_not_awaited()
        board.set("ruled", 11, dispute_id="disp-1", worker_pct=60)
        await loop._cycle()

    mocked.submit_worker_rebuttal.assert_not_awaited()
    assert len(loop._history.records) == 1
    record = loop._history.records[0]
    assert record.outcome == TaskOutcome.DISPUTED_WON
    assert record.payout == 30
    assert record.bid_amount == 40


async def test_restart_after_prepared_rebuttal_sends_the_same_text_once(tmp_path: Path) -> None:
    board = _Board()
    board.set("disputed", 10, dispute_id="disp-1", dispute_reason="Wrong answer")
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        mocked.submit_worker_rebuttal = AsyncMock(side_effect=RuntimeError("died mid-send"))
        loop = MathWorkerLoop(agent, _llm("Prepared rebuttal."), _config(), commands)
        await loop._cycle()

    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        llm = _llm()
        loop = MathWorkerLoop(agent, llm, _config(), commands)
        await loop._cycle()
        await loop._cycle()

    mocked.submit_worker_rebuttal.assert_awaited_once_with(TASK_ID, "disp-1", "Prepared rebuttal.")
    llm.complete.assert_not_awaited()


async def test_server_side_rebuttal_timestamp_prevents_a_second_rebuttal(tmp_path: Path) -> None:
    board = _Board()
    board.set(
        "disputed",
        10,
        dispute_id="disp-1",
        dispute_reason="Wrong answer",
        rebuttal_submitted_at="2026-10-30T12:00:00Z",
    )
    async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
        mocked = _wire(agent, board)
        install_successful_native_transports(agent, store)
        llm = _llm()
        loop = MathWorkerLoop(agent, llm, _config(), commands)
        await loop._cycle()

    mocked.submit_worker_rebuttal.assert_not_awaited()
    llm.complete.assert_not_awaited()


async def test_ruling_payout_uses_worker_pct_of_settlement_amount(tmp_path: Path) -> None:
    board = _Board()
    board.set("ruled", 11, dispute_id="disp-1", worker_pct=0)
    async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
        _wire(agent, board)
        install_successful_native_transports(agent, store)
        commands.jobs.save(
            TASK_ID,
            {
                "phase": "disputed",
                "bid_amount": 40,
                "solution": "42",
                "last_error": None,
                "upload": None,
                "rebuttal": None,
                "outcome": None,
                "payout": None,
            },
        )
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()

    record = loop._history.records[0]
    assert record.outcome == TaskOutcome.DISPUTED_LOST
    assert record.payout == 0
    assert record.bid_amount == 40


async def test_finished_jobs_rebuild_history_after_restart(tmp_path: Path) -> None:
    board = _Board()
    board.set("accepted", 8)
    root = tmp_path / "runtime"
    async with runtime_worker(root) as (agent, store, commands):
        _wire(agent, board)
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm("ANSWER: 42"), _config(), commands)
        await loop._cycle()
        board.set("approved", 10)
        await loop._cycle()

    board.task = task_snapshot("open", 1)
    board.task["task_id"] = TASK_ID
    async with runtime_worker(root) as (agent, store, commands):
        mocked = _wire(agent, board)
        mocked.list_tasks = AsyncMock(return_value=[])
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _llm(), _config(), commands)
        await loop._cycle()

    assert [r.outcome for r in loop._history.records] == [TaskOutcome.APPROVED]
    assert loop._history.total_earnings == 50
