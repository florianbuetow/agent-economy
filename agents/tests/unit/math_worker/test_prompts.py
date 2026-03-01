"""Unit tests for math_worker.prompts."""

import pytest

from math_worker.prompts import (
    build_bid_amount_prompt,
    build_rebuttal_prompt,
    build_solve_prompt,
    build_task_selection_prompt,
)


@pytest.mark.unit
class TestBuildTaskSelectionPrompt:
    def test_includes_balance(self) -> None:
        prompt = build_task_selection_prompt([], balance=500)
        assert "500" in prompt

    def test_includes_task_ids(self) -> None:
        tasks = [
            {"task_id": "t-1", "title": "Add numbers", "reward": 100, "spec": "1+1"},
            {"task_id": "t-2", "title": "Multiply", "reward": 200, "spec": "2*3"},
        ]
        prompt = build_task_selection_prompt(tasks, balance=500)
        assert "t-1" in prompt
        assert "t-2" in prompt

    def test_includes_specs(self) -> None:
        tasks = [{"task_id": "t-1", "title": "X", "reward": 10, "spec": "Solve 2+2"}]
        prompt = build_task_selection_prompt(tasks, balance=100)
        assert "Solve 2+2" in prompt


@pytest.mark.unit
class TestBuildBidAmountPrompt:
    def test_includes_reward_and_balance(self) -> None:
        task = {"task_id": "t-1", "title": "Math", "reward": 100, "spec": "1+1"}
        prompt = build_bid_amount_prompt(task, balance=500)
        assert "100" in prompt
        assert "500" in prompt


@pytest.mark.unit
class TestBuildSolvePrompt:
    def test_includes_spec(self) -> None:
        task = {"task_id": "t-1", "title": "Add", "spec": "Calculate 2+3"}
        prompt = build_solve_prompt(task)
        assert "Calculate 2+3" in prompt

    def test_includes_title(self) -> None:
        task = {"task_id": "t-1", "title": "Big Problem", "spec": "x"}
        prompt = build_solve_prompt(task)
        assert "Big Problem" in prompt


@pytest.mark.unit
class TestBuildRebuttalPrompt:
    def test_includes_all_context(self) -> None:
        task = {"task_id": "t-1", "title": "Math", "spec": "Solve 5+5"}
        prompt = build_rebuttal_prompt(task, "10", "Wrong answer")
        assert "Solve 5+5" in prompt
        assert "10" in prompt
        assert "Wrong answer" in prompt
