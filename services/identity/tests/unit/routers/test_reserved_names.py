"""Reserved display names are rejected unless signed off by a pinned platform key."""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _public_key() -> str:
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{base64.b64encode(raw).decode()}"


def _pinned_platform_key(tmp_path) -> str:
    pem = (tmp_path / "platform.pub").read_bytes()
    public = serialization.load_pem_public_key(pem)
    raw = public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return f"ed25519:{base64.b64encode(raw).decode()}"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["platform", "Operator ", "UI Operator", "PLATFORM"])
async def test_reserved_name_rejected_for_unpinned_key(client, name):
    response = await client.post(
        "/agents/register",
        json={"name": name, "public_key": _public_key()},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "reserved_name"


@pytest.mark.unit
async def test_ordinary_name_accepted(client):
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": _public_key()},
    )
    assert response.status_code == 201


@pytest.mark.unit
async def test_reserved_name_accepted_for_pinned_platform_key(client, tmp_path):
    response = await client.post(
        "/agents/register",
        json={"name": "Platform", "public_key": _pinned_platform_key(tmp_path)},
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Platform"
