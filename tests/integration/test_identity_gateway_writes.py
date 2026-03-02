"""Integration tests: Identity writes via DB Gateway to economy.db."""

from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import count_rows, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _agent_payload(
    *,
    agent_id: str,
    name: str,
    public_key: str,
    registered_at: str,
) -> dict[str, object]:
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": public_key,
        "registered_at": registered_at,
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": registered_at,
            "agent_id": agent_id,
            "summary": f"{name} registered as agent",
            "payload": json.dumps({"agent_name": name}),
        },
    }


@pytest_asyncio.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        yield client


class TestIdentityGatewayWrites:
    async def test_register_agent_creates_identity_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        payload = _agent_payload(
            agent_id="a-test-001",
            name="Alice",
            public_key="ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            registered_at="2026-03-02T06:29:57Z",
        )

        response = await gw_client.post("/identity/agents", json=payload)
        assert response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT agent_id, name, public_key, registered_at "
            "FROM identity_agents WHERE agent_id = ?",
            ("a-test-001",),
        )
        assert row is not None
        assert row["name"] == "Alice"
        assert row["public_key"] == "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        assert row["registered_at"] == "2026-03-02T06:29:57Z"

    async def test_register_agent_creates_event_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        payload = _agent_payload(
            agent_id="a-test-002",
            name="Bob",
            public_key="ed25519:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            registered_at="2026-03-02T06:29:58Z",
        )

        response = await gw_client.post("/identity/agents", json=payload)
        assert response.status_code == 201

        row = read_one(
            initialized_db,
            "SELECT event_source, event_type, summary FROM events WHERE agent_id = ?",
            ("a-test-002",),
        )
        assert row is not None
        assert row["event_source"] == "identity"
        assert row["event_type"] == "agent.registered"
        assert "Bob" in row["summary"]

    async def test_duplicate_public_key_returns_409_and_no_extra_row(
        self,
        gw_client: httpx.AsyncClient,
        initialized_db: str,
    ) -> None:
        response_1 = await gw_client.post(
            "/identity/agents",
            json=_agent_payload(
                agent_id="a-test-003",
                name="Carol",
                public_key="ed25519:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                registered_at="2026-03-02T06:29:59Z",
            ),
        )
        assert response_1.status_code == 201

        response_2 = await gw_client.post(
            "/identity/agents",
            json=_agent_payload(
                agent_id="a-test-003b",
                name="Carol Duplicate",
                public_key="ed25519:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                registered_at="2026-03-02T06:30:00Z",
            ),
        )
        assert response_2.status_code == 409
        assert count_rows(initialized_db, "identity_agents") == 1
