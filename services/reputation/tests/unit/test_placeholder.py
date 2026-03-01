"""Placeholder test to keep pytest from failing with exit code 5 (no tests collected)."""

import pytest

import reputation_service


@pytest.mark.unit
def test_service_scaffolded() -> None:
    """Verify the reputation service package is importable."""
    assert reputation_service is not None
