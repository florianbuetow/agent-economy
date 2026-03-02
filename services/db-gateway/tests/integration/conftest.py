"""Integration test fixtures."""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def gateway_url() -> str:
    """Base URL for the running Database Gateway service."""
    return "http://localhost:8006"


@pytest.fixture
def gateway_client(gateway_url: str) -> httpx.Client:
    """HTTP client for the gateway."""
    return httpx.Client(base_url=gateway_url)
