"""Unit tests for ReviewLoop's asset-based answer fallback.

Discovered while building the T-102 headline e2e: the real Task Board API
never populates ``submitted_answer``/``submission``/``deliverable`` on the
task payload (``services/task-board/.../task_manager.py:_task_to_response``
has no such fields) — the worker's answer lives in an uploaded file asset
(``MathWorkerLoop._phase_submitting`` uploads ``{task_id}_solution.txt``).
Without this fallback, ``review_one()`` always sees an empty submitted
answer against the live API and therefore always disputes, never approves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop

from ._runtime_fixtures import TASK_ID, make_agent, runtime_commands, submitted_task

if TYPE_CHECKING:
    from pathlib import Path


def _make_raw_task(solutions: list[str] | None = None) -> RawTask:
    return RawTask(
        title="Solve 2+2",
        spec="What is 2+2?",
        solutions=solutions if solutions is not None else ["4"],
        level=1,
        problem_type="addition_positive",
        solution_note=None,
    )


def _make_agent_without_payload_answer() -> MagicMock:
    """An agent double matching the REAL Task Board response shape: no
    submitted_answer/submission/deliverable fields on get_task.
    """
    agent = make_agent()
    agent.list_tasks = AsyncMock(return_value=[{"task_id": TASK_ID, "status": "submitted"}])
    agent.get_task = AsyncMock(return_value=submitted_task(None))
    return agent


@pytest.mark.unit
class TestReviewFallsBackToAssetContent:
    @pytest.mark.asyncio
    async def test_correct_answer_in_asset_triggers_approve(self, tmp_path: Path) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[{"asset_id": "asset-1", "uploaded_at": "2026-01-01T00:00:00Z"}]
        )
        agent.download_asset = AsyncMock(return_value=b"4")
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "approved"
        agent.list_assets.assert_awaited_once_with(TASK_ID)
        agent.download_asset.assert_awaited_once_with(TASK_ID, "asset-1")
        assert agent.approve_task.await_args.args == (TASK_ID,)
        agent.dispute_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrong_answer_in_asset_triggers_dispute(self, tmp_path: Path) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[{"asset_id": "asset-1", "uploaded_at": "2026-01-01T00:00:00Z"}]
        )
        agent.download_asset = AsyncMock(return_value=b"999")
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "disputed"
        agent.dispute_task.assert_awaited_once()
        agent.approve_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picks_latest_asset_when_multiple_uploaded(self, tmp_path: Path) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[
                {"asset_id": "asset-old", "uploaded_at": "2026-01-01T00:00:00Z"},
                {"asset_id": "asset-new", "uploaded_at": "2026-01-01T00:00:05Z"},
            ]
        )
        agent.download_asset = AsyncMock(return_value=b"4")
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            await loop.review_one(TASK_ID)

        agent.download_asset.assert_awaited_once_with(TASK_ID, "asset-new")

    @pytest.mark.asyncio
    async def test_no_assets_triggers_dispute(self, tmp_path: Path) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(return_value=[])
        agent.download_asset = AsyncMock()
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "disputed"
        agent.download_asset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_payload_answer_takes_precedence_over_assets(self, tmp_path: Path) -> None:
        """When get_task already returns a submitted_answer, assets are never touched
        (keeps the existing test_review.py mocking style working unmodified)."""
        agent = make_agent()
        agent.get_task = AsyncMock(return_value=submitted_task("4"))
        agent.list_assets = AsyncMock(side_effect=AssertionError("should not be called"))
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "approved"
        agent.list_assets.assert_not_called()
