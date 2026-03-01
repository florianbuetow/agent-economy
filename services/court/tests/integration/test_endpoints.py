"""Integration tests — require running service."""

import pytest


@pytest.mark.integration
def test_placeholder():
    """Placeholder for integration tests that require a running service."""
    pytest.skip("Integration tests require a running service")
