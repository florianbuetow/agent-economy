"""Placeholder test to keep pytest from failing with exit code 5 (no tests collected)."""

import pytest

import central_bank_service


@pytest.mark.unit
def test_service_scaffolded() -> None:
    """Verify the central-bank service package is importable."""
    assert central_bank_service is not None
