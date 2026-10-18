"""Automatic transitions require an explicit non-economic actor identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_board_service.config import DeadlineEvaluationConfig

pytestmark = pytest.mark.unit


def test_deadline_actor_is_required_without_an_implicit_identity() -> None:
    with pytest.raises(ValidationError) as error:
        DeadlineEvaluationConfig.model_validate({"evaluation_interval_seconds": 10})
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("actor_id",), "missing")
    ]


def test_explicit_deadline_actor_is_preserved() -> None:
    value = DeadlineEvaluationConfig.model_validate(
        {"evaluation_interval_seconds": 10, "actor_id": "system-task-board-deadline"}
    )
    assert value.model_dump()["actor_id"] == "system-task-board-deadline"


@pytest.mark.parametrize(
    ("actor_id", "error_type"),
    [
        (None, "string_type"),
        (False, "string_type"),
        (42, "string_type"),
        ("", "value_error"),
        (" \t", "value_error"),
        (" system-task-board-deadline", "value_error"),
        ("system-task-board-deadline ", "value_error"),
    ],
)
def test_invalid_deadline_actor_fails_for_the_identity_field(
    actor_id: object, error_type: str
) -> None:
    with pytest.raises(ValidationError) as error:
        DeadlineEvaluationConfig.model_validate(
            {"evaluation_interval_seconds": 10, "actor_id": actor_id}
        )
    assert [(item["loc"], item["type"]) for item in error.value.errors()] == [
        (("actor_id",), error_type)
    ]
