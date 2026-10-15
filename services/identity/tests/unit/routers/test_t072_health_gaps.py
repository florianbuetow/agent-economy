"""T-072 coverage-vs-spec-ID sweep: closes gaps against
docs/specifications/service-tests/identity-service-tests.md, Category 3
(Read/List/Health) — specifically HEALTH-02 and HEALTH-03.

New file only — existing test files are acceptance tests and must not be
edited (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


@pytest.mark.unit
async def test_health02_registered_count_is_exact(client):
    """HEALTH-02: registered_agents in /health exactly matches the number of registrations."""
    for i in range(3):
        _, public_key = _generate_keypair()
        resp = await client.post(
            "/agents/register", json={"name": f"Agent{i}", "public_key": public_key}
        )
        assert resp.status_code == 201

    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["registered_agents"] == 3


@pytest.mark.unit
@pytest.mark.slow
async def test_health03_uptime_is_monotonic(client):
    """HEALTH-03: uptime_seconds strictly increases across two calls >= 1s apart."""
    first = await client.get("/health")
    assert first.status_code == 200
    first_uptime = first.json()["uptime_seconds"]

    await asyncio.sleep(1.05)

    second = await client.get("/health")
    assert second.status_code == 200
    second_uptime = second.json()["uptime_seconds"]

    assert second_uptime > first_uptime
