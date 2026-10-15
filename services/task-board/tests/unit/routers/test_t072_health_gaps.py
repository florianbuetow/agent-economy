"""T-072 coverage-sweep gap fix for the health endpoint's status breakdown.

Closes HEALTH-04 from ``docs/specifications/service-tests/task-board-service-tests.md``,
which the existing ``test_health.py`` suite does not exercise: its own
"HEALTH-04"-labeled test covers the wrong-HTTP-method case (already covered
generically by HTTP-01 in ``test_security.py``) instead of the spec's actual
HEALTH-04 requirement -- that ``tasks_by_status`` reflects real per-status
counts. This is a new test only -- no existing test file is modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import create_task, make_task_id

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestHealthStatusBreakdownGap:
    """Gap in the Health category: GET /health."""

    @pytest.mark.unit
    async def test_health04_tasks_by_status_reflects_actual_state(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
    ) -> None:
        """HEALTH-04: tasks_by_status reflects real counts after a cancellation."""
        task_id_1 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_1)
        task_id_2 = make_task_id()
        await create_task(client, alice_keypair, alice_agent_id, task_id=task_id_2)

        cancel_payload = {
            "action": "cancel_task",
            "task_id": task_id_2,
            "poster_id": alice_agent_id,
        }
        cancel_token = make_jws_token(alice_keypair[0], alice_agent_id, cancel_payload)
        cancel_resp = await client.post(f"/tasks/{task_id_2}/cancel", json={"token": cancel_token})
        assert cancel_resp.status_code == 200

        resp = await client.get("/health")
        assert resp.status_code == 200
        tasks_by_status = resp.json()["tasks_by_status"]
        assert tasks_by_status["open"] == 1
        assert tasks_by_status["cancelled"] == 1
        for status_key, count in tasks_by_status.items():
            if status_key not in {"open", "cancelled"}:
                assert count == 0, f"expected 0 for {status_key}, got {count}"
