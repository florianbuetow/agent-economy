"""T-072 coverage-sweep gap fixes for the ruling endpoint.

Each test below closes a spec ID from ``docs/specifications/service-tests/
task-board-service-tests.md`` that the existing ``test_disputes.py`` suite does
not actually exercise under its own drifted ``RUL-nn`` docstring labels. These
are new tests only -- no existing test file is modified.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import make_task_id, setup_task_in_dispute

if TYPE_CHECKING:
    from httpx import AsyncClient


class TestRulingGaps:
    """Gaps in Category 12: Ruling (POST /tasks/{task_id}/ruling)."""

    @pytest.mark.unit
    async def test_rul08_empty_ruling_summary_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
        platform_keypair,
        platform_agent_id: str,
    ) -> None:
        """RUL-08: Empty ruling_summary returns 400 invalid_payload."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": f"rul-{uuid.uuid4()}",
            "worker_pct": 50,
            "ruling_summary": "",
        }
        token = make_jws_token(platform_keypair[0], platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_rul09_empty_ruling_id_rejected(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
        platform_keypair,
        platform_agent_id: str,
    ) -> None:
        """RUL-09: Empty ruling_id returns 400 invalid_payload."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": "",
            "worker_pct": 50,
            "ruling_summary": "Empty ruling_id",
        }
        token = make_jws_token(platform_keypair[0], platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"

    @pytest.mark.unit
    async def test_rul13_task_id_payload_must_match_url(
        self,
        client: AsyncClient,
        alice_keypair,
        alice_agent_id: str,
        bob_keypair,
        bob_agent_id: str,
        platform_keypair,
        platform_agent_id: str,
    ) -> None:
        """RUL-13: task_id in payload must match URL path -- returns 400 invalid_payload."""
        task_id = await setup_task_in_dispute(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        other_task_id = make_task_id()

        payload = {
            "action": "record_ruling",
            "task_id": other_task_id,
            "ruling_id": f"rul-{uuid.uuid4()}",
            "worker_pct": 50,
            "ruling_summary": "Mismatched task_id",
        }
        token = make_jws_token(platform_keypair[0], platform_agent_id, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_payload"
