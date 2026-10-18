"""Unit tests for task_feeder.config."""

import pytest
from pydantic import ValidationError

from task_feeder.config import TaskFeederConfig


@pytest.mark.unit
class TestTaskFeederConfig:
    def test_creates_config(self) -> None:
        config = TaskFeederConfig(
            handle="feeder",
            tasks_file="../data/math_tasks.jsonl",
            feed_interval_seconds=15,
            max_open_tasks=5,
            bidding_deadline_seconds=120,
            execution_deadline_seconds=300,
            review_deadline_seconds=120,
            base_reward=10,
            reward_per_level=10,
            shuffle=True,
        )
        assert config.handle == "feeder"
        assert config.max_open_tasks == 5
        assert config.base_reward == 10

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            TaskFeederConfig(
                handle="feeder",
                tasks_file="../data/math_tasks.jsonl",
                feed_interval_seconds=15,
                max_open_tasks=5,
                bidding_deadline_seconds=120,
                execution_deadline_seconds=300,
                review_deadline_seconds=120,
                base_reward=10,
                reward_per_level=10,
                shuffle=True,
                extra_field="bad",  # type: ignore[call-arg]
            )
