"""T-072 coverage-vs-spec-ID sweep: closes gaps against
docs/specifications/service-tests/identity-service-tests.md, Category 4
(HTTP Method and Endpoint Misuse) — HTTP-01, the routes not already exercised
by the acceptance test files (GET /agents/register, GET /agents/verify-jws,
POST /health are covered elsewhere; this file closes the remaining matrix).

New file only — existing test files are acceptance tests and must not be
edited (CLAUDE.md).
"""

from __future__ import annotations

import pytest

_METHOD_ROUTE_CASES = [
    ("PUT", "/agents/register"),
    ("POST", "/agents/a-00000000-0000-0000-0000-000000000000"),
    ("PATCH", "/agents/a-00000000-0000-0000-0000-000000000000"),
    ("DELETE", "/agents/a-00000000-0000-0000-0000-000000000000"),
    ("POST", "/agents"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("method", "path"), _METHOD_ROUTE_CASES)
async def test_http01_wrong_method_on_defined_route_is_blocked(client, method, path):
    """HTTP-01: unsupported HTTP methods on defined routes return 405 method_not_allowed."""
    response = await client.request(method, path)
    assert response.status_code == 405
    assert response.json()["error"] == "method_not_allowed"
