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

from unittest.mock import AsyncMock, MagicMock

import pytest

from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop


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
    agent = MagicMock()
    agent.agent_id = "a-feeder-test"
    agent.list_tasks = AsyncMock(return_value=[{"task_id": "t-1", "status": "submitted"}])
    agent.get_task = AsyncMock(return_value={"task_id": "t-1", "status": "submitted"})
    agent.approve_task = AsyncMock(return_value={"status": "approved"})
    agent.dispute_task = AsyncMock(return_value={"status": "disputed"})
    return agent


@pytest.mark.unit
class TestReviewFallsBackToAssetContent:
    @pytest.mark.asyncio
    async def test_correct_answer_in_asset_triggers_approve(self) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[{"asset_id": "asset-1", "uploaded_at": "2026-01-01T00:00:00Z"}]
        )
        agent.download_asset = AsyncMock(return_value=b"4")
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        result = await loop.review_one("t-1")

        assert result == "approved"
        agent.list_assets.assert_awaited_once_with("t-1")
        agent.download_asset.assert_awaited_once_with("t-1", "asset-1")
        agent.approve_task.assert_awaited_once_with("t-1")
        agent.dispute_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wrong_answer_in_asset_triggers_dispute(self) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[{"asset_id": "asset-1", "uploaded_at": "2026-01-01T00:00:00Z"}]
        )
        agent.download_asset = AsyncMock(return_value=b"999")
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        result = await loop.review_one("t-1")

        assert result == "disputed"
        agent.dispute_task.assert_awaited_once()
        agent.approve_task.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picks_latest_asset_when_multiple_uploaded(self) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(
            return_value=[
                {"asset_id": "asset-old", "uploaded_at": "2026-01-01T00:00:00Z"},
                {"asset_id": "asset-new", "uploaded_at": "2026-01-01T00:00:05Z"},
            ]
        )
        agent.download_asset = AsyncMock(return_value=b"4")
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        await loop.review_one("t-1")

        agent.download_asset.assert_awaited_once_with("t-1", "asset-new")

    @pytest.mark.asyncio
    async def test_no_assets_triggers_dispute(self) -> None:
        agent = _make_agent_without_payload_answer()
        agent.list_assets = AsyncMock(return_value=[])
        agent.download_asset = AsyncMock()
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        result = await loop.review_one("t-1")

        assert result == "disputed"
        agent.download_asset.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_payload_answer_takes_precedence_over_assets(self) -> None:
        """When get_task already returns a submitted_answer, assets are never touched
        (keeps the existing test_review.py mocking style working unmodified)."""
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"task_id": "t-1", "submitted_answer": "4"})
        agent.list_assets = AsyncMock(side_effect=AssertionError("should not be called"))
        agent.approve_task = AsyncMock(return_value={"status": "approved"})
        agent.dispute_task = AsyncMock(return_value={"status": "disputed"})
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        result = await loop.review_one("t-1")

        assert result == "approved"
        agent.list_assets.assert_not_called()
