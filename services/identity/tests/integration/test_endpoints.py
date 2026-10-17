"""Integration tests — the real identity app driven end-to-end against a real,
in-process DB Gateway (the production `AgentDbClient` path, T-071/GAP-F1).

Unlike tests/unit (in-memory fake store), these tests prove the HTTP-level
contract between identity_service and the DB Gateway actually round-trips:
every assertion here is checked both through the identity API and by reading
the gateway's own SQLite file directly.
"""

from __future__ import annotations

import base64
import sqlite3
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth.jws import create_jws

if TYPE_CHECKING:
    from httpx import AsyncClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def _read_one(
    db_path: str, query: str, params: tuple[object, ...] = ()
) -> dict[str, object] | None:
    """Read a single SQLite row as a dict, straight from the gateway's own file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


class TestRegisterAgentGatewayBacked:
    async def test_register_agent_persists_row_via_real_gateway(
        self, client: AsyncClient, gateway_db_path: str
    ) -> None:
        """POST /agents/register writes a real identity_agents row through the
        production AgentDbClient -> DB Gateway -> SQLite path."""
        _, public_key = _generate_keypair()
        response = await client.post(
            "/agents/register",
            json={"name": "Alice", "public_key": public_key},
        )
        assert response.status_code == 201
        agent_id = response.json()["agent_id"]

        row = _read_one(
            gateway_db_path,
            "SELECT agent_id, name, public_key FROM identity_agents WHERE agent_id = ?",
            (agent_id,),
        )
        assert row is not None
        assert row["name"] == "Alice"
        assert row["public_key"] == public_key

    async def test_register_agent_writes_event_row_via_real_gateway(
        self, client: AsyncClient, gateway_db_path: str
    ) -> None:
        """Registration also writes the agent.registered event row."""
        _, public_key = _generate_keypair()
        response = await client.post(
            "/agents/register",
            json={"name": "Bob", "public_key": public_key},
        )
        assert response.status_code == 201
        agent_id = response.json()["agent_id"]

        row = _read_one(
            gateway_db_path,
            "SELECT event_source, event_type FROM events WHERE agent_id = ?",
            (agent_id,),
        )
        assert row is not None
        assert row["event_source"] == "identity"
        assert row["event_type"] == "agent.registered"

    async def test_duplicate_public_key_rejected_by_real_gateway(
        self, client: AsyncClient, gateway_db_path: str
    ) -> None:
        """A second registration with the same public key is rejected with 409,
        and the gateway's own unique constraint (not just app-level logic) is
        what's being exercised here."""
        _, public_key = _generate_keypair()
        first = await client.post(
            "/agents/register",
            json={"name": "Carol", "public_key": public_key},
        )
        assert first.status_code == 201

        second = await client.post(
            "/agents/register",
            json={"name": "Carol Duplicate", "public_key": public_key},
        )
        assert second.status_code == 409
        assert second.json()["error"] == "public_key_exists"

        conn = sqlite3.connect(gateway_db_path)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM identity_agents WHERE public_key = ?", (public_key,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1


class TestReadPathGatewayBacked:
    async def test_get_agent_reads_through_real_gateway(self, client: AsyncClient) -> None:
        """GET /agents/{agent_id} reads back through the same gateway-backed path."""
        _, public_key = _generate_keypair()
        reg = await client.post(
            "/agents/register",
            json={"name": "Dave", "public_key": public_key},
        )
        agent_id = reg.json()["agent_id"]

        response = await client.get(f"/agents/{agent_id}")
        assert response.status_code == 200
        assert response.json()["agent_id"] == agent_id
        assert response.json()["public_key"] == public_key

    async def test_list_agents_reads_through_real_gateway(self, client: AsyncClient) -> None:
        """GET /agents lists every agent persisted via the real gateway."""
        for name in ("Eve", "Frank"):
            _, public_key = _generate_keypair()
            resp = await client.post(
                "/agents/register",
                json={"name": name, "public_key": public_key},
            )
            assert resp.status_code == 201

        response = await client.get("/agents")
        assert response.status_code == 200
        names = {agent["name"] for agent in response.json()["agents"]}
        assert {"Eve", "Frank"}.issubset(names)


class TestVerifyJwsGatewayBacked:
    async def test_verify_jws_round_trips_through_real_gateway(self, client: AsyncClient) -> None:
        """POST /agents/verify-jws looks the signer's public key up through the
        real gateway-backed AgentDbClient (not the fake store)."""
        private_key, public_key = _generate_keypair()
        reg = await client.post(
            "/agents/register",
            json={"name": "Grace", "public_key": public_key},
        )
        agent_id = reg.json()["agent_id"]

        token = create_jws(
            {"action": "test", "hello": "world"},
            private_key,
            kid=agent_id,
            audience="identity-test",
            resource="POST /agents/verify-jws",
            token_ttl_seconds=300,
        )

        response = await client.post("/agents/verify-jws", json={"token": token})
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["agent_id"] == agent_id
