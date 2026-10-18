"""The feeder supplies the poster's explicit native acceptance window."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from pydantic import ValidationError

from task_feeder.config import TaskFeederConfig, load_task_feeder_settings
from task_feeder.loop import TaskFeederLoop
from task_feeder.reader import RawTask

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _values() -> dict[str, object]:
    return {
        "handle": "feeder",
        "tasks_file": "tasks.jsonl",
        "feed_interval_seconds": 5,
        "max_open_tasks": 3,
        "bidding_deadline_seconds": 120,
        "acceptance_window_seconds": 73,
        "execution_deadline_seconds": 300,
        "review_deadline_seconds": 120,
        "review_interval_seconds": 30,
        "auto_approve_on_error": False,
        "base_reward": 10,
        "reward_per_level": 10,
        "shuffle": False,
    }


@pytest.mark.parametrize("duration", [1, 73, 9_223_372_036_854])
def test_explicit_positive_sqlite_safe_window_is_retained(duration: int) -> None:
    config = TaskFeederConfig.model_validate({**_values(), "acceptance_window_seconds": duration})
    assert config.model_dump()["acceptance_window_seconds"] == duration


def test_missing_window_is_rejected_for_the_missing_field() -> None:
    values = _values()
    del values["acceptance_window_seconds"]
    with pytest.raises(ValidationError) as raised:
        TaskFeederConfig.model_validate(values)
    assert [(error["loc"], error["type"]) for error in raised.value.errors()] == [
        (("acceptance_window_seconds",), "missing")
    ]


@pytest.mark.parametrize("duration", [True, False, "73", 1.5, None, 0, -1, 9_223_372_036_855])
def test_invalid_window_is_rejected_as_a_duration(duration: object) -> None:
    with pytest.raises(ValidationError) as raised:
        TaskFeederConfig.model_validate({**_values(), "acceptance_window_seconds": duration})
    errors = raised.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("acceptance_window_seconds",)
    assert errors[0]["type"] in {"int_type", "greater_than_equal", "less_than_equal"}


def test_window_and_selection_delay_are_distinct_yaml_fields(tmp_path: Path) -> None:
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "task_feeder": {
                    **_values(),
                    "acceptance_after_seconds": 17,
                    "acceptance_poll_interval_seconds": 5,
                    "min_bids_to_accept": 2,
                }
            }
        ),
        encoding="utf-8",
    )
    config = load_task_feeder_settings(path)
    assert config.model_dump()["acceptance_window_seconds"] == 73


async def test_posting_forwards_the_exact_poster_window(monkeypatch: pytest.MonkeyPatch) -> None:
    config = TaskFeederConfig.model_validate(_values())
    agent = Mock()
    agent.agent_id = "a-feeder"
    agent.list_tasks = AsyncMock(return_value=[])
    agent.post_task = AsyncMock(return_value={"task_id": "t-posted"})
    sleep = AsyncMock()
    monkeypatch.setattr("task_feeder.loop.asyncio.sleep", sleep)
    task = RawTask(
        title="Addition",
        spec="Calculate 1 + 2",
        level=1,
        problem_type="addition",
        solutions=["3"],
        solution_note=None,
    )
    loop = TaskFeederLoop(agent=agent, config=config)
    await loop._feed_one(iter([task]))
    agent.post_task.assert_awaited_once_with(
        title=task.title,
        spec=task.spec,
        reward=20,
        bidding_deadline_seconds=120,
        acceptance_window_seconds=73,
        execution_deadline_seconds=300,
        review_deadline_seconds=120,
    )
    assert loop.task_map == {"t-posted": task}
    sleep.assert_awaited_once_with(5)
