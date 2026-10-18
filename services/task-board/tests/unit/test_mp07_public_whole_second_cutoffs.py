"""Whole-second public cutoffs retain the established UTC-Z representation."""

import pytest

from task_board_service.services.task_helpers import task_to_response, task_to_summary
from tests.unit.test_mp07_public_task_projection import _task_row

pytestmark = pytest.mark.unit


def test_whole_second_cutoffs_retain_existing_public_format() -> None:
    task = _task_row(legacy=False)
    for field in (
        "bidding_cutoff_us",
        "acceptance_cutoff_us",
        "execution_cutoff_us",
        "review_cutoff_us",
    ):
        task[field] -= task[field] % 1_000_000
    expected = {
        "bidding_deadline": "2099-01-01T00:00:00Z",
        "acceptance_deadline": "2099-01-01T00:00:30Z",
        "execution_deadline": "2099-01-01T00:02:01Z",
        "review_deadline": "2099-01-01T00:03:02Z",
    }
    for projected in (task_to_response(task), task_to_summary(task)):
        assert {key: projected[key] for key in expected} == expected
