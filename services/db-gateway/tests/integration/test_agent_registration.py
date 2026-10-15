"""Integration tests — require a running Database Gateway service.

Extends the health-only coverage in test_endpoints.py (GAP-F1/T-071): a real
write-then-read round trip and a duplicate-conflict check against a live,
network-bound gateway process — not the in-process ASGITransport fakes the
per-service *DbClient contract suites use elsewhere.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx


def _agent_payload(agent_id: str, name: str) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": f"ed25519:{uuid.uuid4()}",
        "registered_at": "2026-03-01T09:00:00Z",
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": "2026-03-01T09:00:00Z",
            "agent_id": agent_id,
            "summary": f"{name} registered",
            "payload": "{}",
        },
    }


@pytest.mark.integration
class TestAgentRegistrationIntegration:
    """Write-then-read round trip and duplicate-conflict handling over a
    real, live network connection to the Database Gateway."""

    def test_register_then_read_round_trips_over_live_http(
        self, gateway_client: httpx.Client
    ) -> None:
        """POST /identity/agents, then GET the same agent back — over real
        HTTP, not an in-process transport."""
        agent_id = f"a-gw-integration-{uuid.uuid4()}"
        create_resp = gateway_client.post(
            "/identity/agents", json=_agent_payload(agent_id, "Live Gateway Test Agent")
        )
        assert create_resp.status_code == 201, create_resp.text

        read_resp = gateway_client.get(f"/identity/agents/{agent_id}")
        assert read_resp.status_code == 200
        data = read_resp.json()
        assert data["agent_id"] == agent_id
        assert data["name"] == "Live Gateway Test Agent"

    def test_duplicate_public_key_returns_409_over_live_http(
        self, gateway_client: httpx.Client
    ) -> None:
        """A second registration reusing the same public_key is rejected —
        proves the live server's own unique constraint, not an app-layer check."""
        agent_id_1 = f"a-gw-integration-{uuid.uuid4()}"
        agent_id_2 = f"a-gw-integration-{uuid.uuid4()}"
        shared_public_key = f"ed25519:{uuid.uuid4()}"

        payload_1 = _agent_payload(agent_id_1, "First")
        payload_1["public_key"] = shared_public_key
        resp_1 = gateway_client.post("/identity/agents", json=payload_1)
        assert resp_1.status_code == 201, resp_1.text

        payload_2 = _agent_payload(agent_id_2, "Second")
        payload_2["public_key"] = shared_public_key
        resp_2 = gateway_client.post("/identity/agents", json=payload_2)
        assert resp_2.status_code == 409
        assert resp_2.json()["error"] == "public_key_exists"

    def test_health_total_events_increments_after_live_write(
        self, gateway_client: httpx.Client
    ) -> None:
        """GET /health's total_events reflects a write just made over live HTTP."""
        before = gateway_client.get("/health").json()["total_events"]

        agent_id = f"a-gw-integration-{uuid.uuid4()}"
        resp = gateway_client.post("/identity/agents", json=_agent_payload(agent_id, "Counted"))
        assert resp.status_code == 201, resp.text

        after = gateway_client.get("/health").json()["total_events"]
        assert after == before + 1
