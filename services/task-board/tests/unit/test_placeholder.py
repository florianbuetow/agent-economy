"""Placeholder test to keep pytest from failing with exit code 5 (no tests collected)."""

import pytest

import task_board_service


@pytest.mark.unit
def test_service_scaffolded() -> None:
    """Verify the task-board service package is importable."""
    assert task_board_service is not None
