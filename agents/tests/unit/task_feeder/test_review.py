from __future__ import annotations

from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from task_feeder.reader import RawTask
from task_feeder.review import ReviewLoop, check_answer


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
    return {
        "task_id": "t-1",
        "submitted_answer": submitted_answer,
        "submission": {"answer": submitted_answer},
        "deliverable": {"answer": submitted_answer},
    }


def _make_agent(submitted_answer: str = "4") -> MagicMock:
    agent = MagicMock()
    agent.agent_id = "a-feeder-test"
    agent.list_tasks = AsyncMock(return_value=[{"task_id": "t-1", "status": "submitted"}])
    agent.get_task = AsyncMock(return_value=_make_task_payload(submitted_answer))
    agent.approve_task = AsyncMock(return_value={"status": "approved"})
    agent.dispute_task = AsyncMock(return_value={"status": "disputed"})
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
    async def test_correct_answer_triggers_approve(self) -> None:
        agent = _make_agent(submitted_answer="4")
        task_map = {"t-1": _make_raw_task(solutions=["4"])}
        loop = ReviewLoop(agent, task_map)

        result = await loop.review_one("t-1")

        assert result == "approved"
        agent.approve_task.assert_called_once_with("t-1")
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_incorrect_answer_triggers_dispute(self) -> None:
        agent = _make_agent(submitted_answer="42")
        task_map = {"t-1": _make_raw_task(solutions=["4"])}
        loop = ReviewLoop(agent, task_map)

        result = await loop.review_one("t-1")

        assert result == "disputed"
        agent.dispute_task.assert_called_once()
        call_args = agent.dispute_task.call_args
        assert call_args is not None
        reason = call_args.args[1]
        assert "Expected" in reason
        assert "got" in reason
        assert "42" in reason

    @pytest.mark.asyncio
    async def test_case_insensitive_comparison_in_review(self) -> None:
        agent = _make_agent(submitted_answer="X=15")
        task_map = {"t-1": _make_raw_task(solutions=["x=15", "15"])}
        loop = ReviewLoop(agent, task_map)

        result = await loop.review_one("t-1")

        assert result == "approved"
        agent.approve_task.assert_called_once_with("t-1")
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_trimmed_comparison_in_review(self) -> None:
        agent = _make_agent(submitted_answer="  15  ")
        task_map = {"t-1": _make_raw_task(solutions=["15"])}
        loop = ReviewLoop(agent, task_map)

        result = await loop.review_one("t-1")

        assert result == "approved"
        agent.approve_task.assert_called_once_with("t-1")
        agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_valid_solutions_accepted_in_review(self) -> None:
        first_agent = _make_agent(submitted_answer="x=15")
        first_loop = ReviewLoop(first_agent, {"t-1": _make_raw_task(solutions=["x=15", "15"])})
        first_result = await first_loop.review_one("t-1")
        assert first_result == "approved"
        first_agent.approve_task.assert_called_once_with("t-1")
        first_agent.dispute_task.assert_not_called()

        second_agent = _make_agent(submitted_answer="15")
        second_loop = ReviewLoop(second_agent, {"t-1": _make_raw_task(solutions=["x=15", "15"])})
        second_result = await second_loop.review_one("t-1")
        assert second_result == "approved"
        second_agent.approve_task.assert_called_once_with("t-1")
        second_agent.dispute_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_map_lookup(self) -> None:
        agent = _make_agent(submitted_answer="4")
        task_1 = _make_raw_task(title="Task 1")
        task_2 = _make_raw_task(title="Task 2", solutions=["8"])
        task_map = {"t-1": task_1, "t-2": task_2}
        loop = ReviewLoop(agent, task_map)

        stored_map = getattr(loop, "_task_map", None)
        if stored_map is None:
            stored_map = getattr(loop, "task_map", None)

        assert stored_map is not None
        assert stored_map["t-1"] is task_1
        assert stored_map["t-2"] is task_2

    @pytest.mark.asyncio
    async def test_unknown_task_id_skipped(self) -> None:
        agent = _make_agent(submitted_answer="4")
        loop = ReviewLoop(agent, {"t-1": _make_raw_task(solutions=["4"])})

        with (
            patch.object(agent, "approve_task", wraps=agent.approve_task) as approve_spy,
            patch.object(agent, "dispute_task", wraps=agent.dispute_task) as dispute_spy,
            suppress(LookupError),
        ):
            await loop.review_one("t-unknown")

        approve_spy.assert_not_called()
        dispute_spy.assert_not_called()
