from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop, check_answer

from ._runtime_fixtures import (
    SECOND_TASK_ID,
    TASK_ID,
    make_agent,
    runtime_commands,
    submitted_task,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_raw_task(
    title: str = "Solve 2+2",
    spec: str = "What is 2+2?",
    solutions: list[str] | None = None,
    level: int = 1,
    problem_type: str = "addition_positive",
    solution_note: str | None = None,
) -> RawTask:
    return RawTask(
        title=title,
        spec=spec,
        solutions=solutions if solutions is not None else ["4"],
        level=level,
        problem_type=problem_type,
        solution_note=solution_note,
    )


def _make_task_payload(submitted_answer: str) -> dict[str, object]:
    return submitted_task(submitted_answer)


def _make_agent(submitted_answer: str = "4") -> MagicMock:
    agent = make_agent()
    agent.list_tasks = AsyncMock(return_value=[{"task_id": TASK_ID, "status": "submitted"}])
    agent.get_task = AsyncMock(return_value=_make_task_payload(submitted_answer))
    return agent


@pytest.mark.unit
class TestCheckAnswer:
    def test_exact_match(self) -> None:
        assert check_answer("4", ["4"])

    def test_case_insensitive_match(self) -> None:
        assert check_answer("X=15", ["x=15", "15"])

    def test_whitespace_trimmed_match(self) -> None:
        assert check_answer("  15  ", ["x=15", "15"])

    def test_combined_case_and_whitespace(self) -> None:
        assert check_answer("  X=15  ", ["x=15", "15"])

    def test_no_match(self) -> None:
        assert not check_answer("42", ["x=15", "15"])

    def test_multiple_valid_solutions_all_accepted(self) -> None:
        assert check_answer("x=15", ["x=15", "15"])
        assert check_answer("15", ["x=15", "15"])

    def test_empty_submitted_answer(self) -> None:
        assert not check_answer("", ["4"])

    def test_empty_solutions_list(self) -> None:
        assert not check_answer("4", [])


@pytest.mark.unit
class TestReviewLoop:
    @pytest.mark.asyncio
    async def test_correct_answer_triggers_approve(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="4")
        task_map = {TASK_ID: _make_raw_task(solutions=["4"])}
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, task_map, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "approved"
        assert agent.approve_task.await_args.args == (TASK_ID,)
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_incorrect_answer_triggers_dispute(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="42")
        task_map = {TASK_ID: _make_raw_task(solutions=["4"])}
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, task_map, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "disputed"
        agent.dispute_task.assert_called_once()
        call_args = agent.dispute_task.call_args
        assert call_args is not None
        reason = call_args.args[1]
        assert "Expected" in reason
        assert "got" in reason
        assert "42" in reason

    @pytest.mark.asyncio
    async def test_case_insensitive_comparison_in_review(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="X=15")
        task_map = {TASK_ID: _make_raw_task(solutions=["x=15", "15"])}
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, task_map, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "approved"
        assert agent.approve_task.await_args.args == (TASK_ID,)
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_trimmed_comparison_in_review(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="  15  ")
        task_map = {TASK_ID: _make_raw_task(solutions=["15"])}
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, task_map, commands)
            result = await loop.review_one(TASK_ID)

        assert result == "approved"
        assert agent.approve_task.await_args.args == (TASK_ID,)
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_valid_solutions_accepted_in_review(self, tmp_path: Path) -> None:
        first_agent = _make_agent(submitted_answer="x=15")
        with runtime_commands(tmp_path / "first", first_agent) as (first_commands, _store):
            first_loop = ReviewLoop(
                first_agent,
                {TASK_ID: _make_raw_task(solutions=["x=15", "15"])},
                first_commands,
            )
            first_result = await first_loop.review_one(TASK_ID)
        assert first_result == "approved"
        assert first_agent.approve_task.await_args.args == (TASK_ID,)
        first_agent.dispute_task.assert_not_called()

        second_agent = _make_agent(submitted_answer="15")
        with runtime_commands(tmp_path / "second", second_agent) as (second_commands, _store):
            second_loop = ReviewLoop(
                second_agent,
                {TASK_ID: _make_raw_task(solutions=["x=15", "15"])},
                second_commands,
            )
            second_result = await second_loop.review_one(TASK_ID)
        assert second_result == "approved"
        assert second_agent.approve_task.await_args.args == (TASK_ID,)
        second_agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_map_lookup(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="4")
        task_1 = _make_raw_task(title="Task 1")
        task_2 = _make_raw_task(title="Task 2", solutions=["8"])
        task_map = {TASK_ID: task_1, SECOND_TASK_ID: task_2}
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, task_map, commands)

        stored_map = getattr(loop, "_task_map", None)
        if stored_map is None:
            stored_map = getattr(loop, "task_map", None)

        assert stored_map is not None
        assert stored_map[TASK_ID] is task_1
        assert stored_map[SECOND_TASK_ID] is task_2

    @pytest.mark.asyncio
    async def test_unknown_task_id_skipped(self, tmp_path: Path) -> None:
        agent = _make_agent(submitted_answer="4")
        with runtime_commands(tmp_path, agent) as (commands, store):
            loop = ReviewLoop(agent, {TASK_ID: _make_raw_task(solutions=["4"])}, commands)
            with (
                patch.object(agent, "approve_task", wraps=agent.approve_task) as approve_spy,
                patch.object(agent, "dispute_task", wraps=agent.dispute_task) as dispute_spy,
                suppress(LookupError),
            ):
                await loop.review_one(SECOND_TASK_ID)
            assert store.load(SECOND_TASK_ID, "review-decision") is None

        approve_spy.assert_not_called()
        dispute_spy.assert_not_called()
