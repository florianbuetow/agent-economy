"""Unit tests for task_feeder.loop — reward computation."""

import pytest

from task_feeder.config import TaskFeederConfig
from task_feeder.loop import TaskFeederLoop


def _make_config(**overrides: object) -> TaskFeederConfig:
    defaults = {
        "handle": "feeder",
        "tasks_file": "../data/math_tasks.jsonl",
        "feed_interval_seconds": 15,
        "max_open_tasks": 5,
        "bidding_deadline_seconds": 120,
        "execution_deadline_seconds": 300,
        "review_deadline_seconds": 120,
        "base_reward": 10,
        "reward_per_level": 10,
        "shuffle": True,
    }
    defaults.update(overrides)
    return TaskFeederConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestRewardComputation:
    def test_level_1_reward(self) -> None:
        config = _make_config(base_reward=10, reward_per_level=10)
        # We need a loop instance to call _compute_reward, but we don't
        # need a real agent — just test the formula.
        loop = TaskFeederLoop.__new__(TaskFeederLoop)
        loop._config = config
        assert loop._compute_reward(1) == 20  # 10 + 1*10

    def test_level_9_reward(self) -> None:
        config = _make_config(base_reward=10, reward_per_level=10)
        loop = TaskFeederLoop.__new__(TaskFeederLoop)
        loop._config = config
        assert loop._compute_reward(9) == 100  # 10 + 9*10

    def test_custom_reward_scale(self) -> None:
        config = _make_config(base_reward=50, reward_per_level=25)
        loop = TaskFeederLoop.__new__(TaskFeederLoop)
        loop._config = config
        assert loop._compute_reward(5) == 175  # 50 + 5*25
