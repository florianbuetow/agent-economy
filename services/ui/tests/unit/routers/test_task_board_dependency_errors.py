"""Regression for preserving typed Task Board dependency failures."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.unit


class _UnavailableCourtAgent:
    agent_id = "a-ui-operator"

    def __init__(self, body: object) -> None:
        self.body = body

    async def dispute_task(self, **_kwargs: Any) -> None:
        request = httpx.Request(
            "POST",
            "http://task-board.test/tasks/t-court-outage/dispute",
        )
        response = httpx.Response(
            502,
            json=self.body,
            request=request,
        )
        response.raise_for_status()


async def test_dispute_preserves_typed_task_board_dependency_error(client) -> None:
    """A valid Task Board 5xx envelope remains actionable through the UI."""
    get_app_state().user_agent = _UnavailableCourtAgent(
        {
            "error": "court_unavailable",
            "message": "Court claim delivery failed",
            "details": {},
        }
    )

    response = await client.post(
        "/api/proxy/tasks/t-court-outage/dispute",
        json={
            "reason": "The submitted result does not satisfy the specification.",
            "operation_id": "op-00000000-0000-4000-8000-000000000223",
            "expected_lifecycle_version": 2,
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": "court_unavailable",
        "message": "Court claim delivery failed",
        "details": {},
    }


async def test_dispute_sanitizes_malformed_task_board_502(client) -> None:
    """An invalid 5xx body cannot leak upstream implementation details."""
    get_app_state().user_agent = _UnavailableCourtAgent(
        {"error": "internal", "private_marker": "must-not-leak"}
    )

    response = await client.post(
        "/api/proxy/tasks/t-court-outage/dispute",
        json={
            "reason": "The submitted result does not satisfy the specification.",
            "operation_id": "op-00000000-0000-4000-8000-000000000224",
            "expected_lifecycle_version": 2,
        },
    )

    assert response.status_code == 502
    assert response.json() == {
        "error": "dispute_filing_failed",
        "message": "Task Board returned a malformed error response",
        "details": {},
    }
    assert "must-not-leak" not in response.text
