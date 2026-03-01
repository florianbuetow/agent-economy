"""Placeholder test to keep pytest from failing with exit code 5 (no tests collected)."""

import pytest

import court_service


@pytest.mark.unit
def test_service_scaffolded() -> None:
    """Verify the court service package is importable."""
    assert court_service is not None
