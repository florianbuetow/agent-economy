"""T-072 coverage sweep: HTTP-01 requires 405 for 13 method/route combinations.

Existing tests (``test_feedback.py``, ``test_endpoints.py``) only exercise 3 of
the 13: ``PUT /feedback``, ``DELETE /feedback/{feedback_id}``, and
``POST /health``. This file covers the remaining 10.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

_MISSING_METHOD_ROUTE_COMBOS = [
    ("GET", "/feedback"),
    ("DELETE", "/feedback"),
    ("POST", "/feedback/fb-test-id"),
    ("PUT", "/feedback/fb-test-id"),
    ("POST", "/feedback/task/task-1"),
    ("PUT", "/feedback/task/task-1"),
    ("DELETE", "/feedback/task/task-1"),
    ("POST", "/feedback/agent/a-test-agent"),
    ("PUT", "/feedback/agent/a-test-agent"),
    ("DELETE", "/feedback/agent/a-test-agent"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("method", "path"), _MISSING_METHOD_ROUTE_COMBOS)
async def test_http_01_unsupported_method_returns_405(
    client: AsyncClient, method: str, path: str
) -> None:
    """HTTP-01: unsupported HTTP methods on defined routes return 405."""
    response = await client.request(method, path)
    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"
