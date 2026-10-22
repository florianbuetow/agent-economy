"""The durable feeder snapshot has an explicit strictly typed byte budget."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from task_feeder.config import TaskFeederConfig

pytestmark = pytest.mark.unit


def _config() -> dict[str, Any]:
    return {
        "handle": "feeder",
        "tasks_file": "/tmp/tasks.jsonl",
        "feed_interval_seconds": 15,
        "max_open_tasks": 5,
        "bidding_deadline_seconds": 120,
        "acceptance_window_seconds": 120,
        "execution_deadline_seconds": 300,
        "review_deadline_seconds": 120,
        "review_interval_seconds": 30,
        "auto_approve_on_error": False,
        "base_reward": 10,
        "reward_per_level": 10,
        "shuffle": True,
    }


def test_snapshot_byte_budget_is_required() -> None:
    with pytest.raises(ValidationError) as error:
        TaskFeederConfig.model_validate(_config())
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("source_max_bytes",), "missing")
    ]


@pytest.mark.parametrize("limit", [1, 8_388_608])
def test_explicit_positive_snapshot_budget_is_preserved(limit: int) -> None:
    config = TaskFeederConfig.model_validate({**_config(), "source_max_bytes": limit})
    assert config.model_dump()["source_max_bytes"] == limit


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (0, "greater_than_equal"),
        (-1, "greater_than_equal"),
        (True, "int_type"),
        (False, "int_type"),
        (1.0, "int_type"),
        ("1", "int_type"),
        (None, "int_type"),
    ],
)
def test_invalid_snapshot_budget_fails_for_that_field(value: object, reason: str) -> None:
    with pytest.raises(ValidationError) as error:
        TaskFeederConfig.model_validate({**_config(), "source_max_bytes": value})
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("source_max_bytes",), reason)
    ]
