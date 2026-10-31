"""The feeder supplies the poster's explicit native acceptance window."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
import yaml
from pydantic import ValidationError

from task_feeder.config import TaskFeederConfig, load_task_feeder_settings
from task_feeder.loop import TaskFeederLoop
from task_feeder.posting import TaskFeederPostingCoordinator
from task_feeder.reader import RawTask, load_task_snapshot

from ._runtime_fixtures import make_agent
from .test_mp05_durable_posting import (
    _configure_prepare,
    _posting_session,
    _published_response,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _values() -> dict[str, object]:
    return {
        "handle": "feeder",
        "tasks_file": "tasks.jsonl",
        "source_max_bytes": 8_388_608,
        "feed_interval_seconds": 5,
        "max_open_tasks": 3,
        "bidding_deadline_seconds": 120,
        "acceptance_window_seconds": 73,
        "execution_deadline_seconds": 300,
        "review_deadline_seconds": 120,
        "review_interval_seconds": 30,
        "retry_backoff_seconds": 1,
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


async def test_posting_forwards_the_exact_poster_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "tasks.jsonl"
    task = RawTask(
        title="Addition",
        spec="Calculate 1 + 2",
        level=1,
        problem_type="addition",
        solutions=["3"],
        solution_note=None,
    )
    source_path.write_text(json.dumps(task.as_json()) + "\n", encoding="utf-8")
    snapshot = load_task_snapshot(source_path.resolve(), max_bytes=8_388_608)
    config = TaskFeederConfig.model_validate({**_values(), "tasks_file": str(source_path)})
    agent = make_agent()
    prepared = _configure_prepare(agent)
    agent.submit_task_posting = AsyncMock(side_effect=_published_response)

    with _posting_session(tmp_path, agent) as postings:
        coordinator = TaskFeederPostingCoordinator(agent, postings)
        coordinator.bind_current_source(snapshot, config)
        loop = TaskFeederLoop(agent, config, coordinator)
        await loop.feed_once()
        saved = postings.records()
        agent.list_tasks.return_value = [{"task_id": f"open-{index}"} for index in range(3)]
        sleep = AsyncMock(side_effect=lambda _seconds: loop.stop())
        monkeypatch.setattr("task_feeder.loop.asyncio.sleep", sleep)
        await loop.run()
        sleep.assert_awaited_once_with(5)

    assert len(prepared) == len(saved) == 1
    expected_terms = (
        "Addition",
        "Calculate 1 + 2",
        20,
        120,
        73,
        300,
        120,
    )
    for intent in (prepared[0], saved[0].intent):
        assert (
            intent.title,
            intent.spec,
            intent.reward,
            intent.bidding_deadline_seconds,
            intent.acceptance_window_seconds,
            intent.execution_deadline_seconds,
            intent.review_deadline_seconds,
        ) == expected_terms
    assert saved[0].state == "published"
    assert loop.task_map == {saved[0].intent.task_id: task}
