"""T-072 coverage-vs-spec-ID sweep: closes gaps against
docs/specifications/service-tests/identity-service-tests.md, Category 3
(Read/List/Health) — specifically READ-03.

New file only — existing test files are acceptance tests and must not be
edited (CLAUDE.md).
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
async def test_read03_malformed_id_returns_404_without_leakage(client):
    """READ-03: a malformed agent_id is rejected with 404, no internal diagnostics leaked."""
    response = await client.get("/agents/not-a-valid-id")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "agent_not_found"
    assert "Traceback" not in body["message"]
    assert "/Users" not in body["message"]
    assert ".py" not in body["message"]


@pytest.mark.unit
async def test_read03_path_traversal_id_does_not_break_routing(client):
    """READ-03: a path-traversal-shaped agent_id is rejected, not routed outside /agents."""
    response = await client.get("/agents/../../etc/passwd")
    assert response.status_code == 404
    body = response.json()
    assert "Traceback" not in str(body)
    assert "root:" not in str(body)
