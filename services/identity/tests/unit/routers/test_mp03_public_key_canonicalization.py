"""Identity canonicalizes equivalent Ed25519 public-key encodings."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_BASE64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _canonical_key_and_pad_bit_alias() -> tuple[str, str]:
    raw_public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    canonical_payload = base64.b64encode(raw_public_key).decode("ascii")
    assert canonical_payload.endswith("=")
    final_data_index = _BASE64_ALPHABET.index(canonical_payload[-2])
    assert final_data_index % 4 == 0
    alias_payload = (
        canonical_payload[:-2] + _BASE64_ALPHABET[final_data_index + 1] + canonical_payload[-1]
    )
    assert alias_payload != canonical_payload
    assert base64.b64decode(alias_payload, validate=True) == raw_public_key
    return f"ed25519:{canonical_payload}", f"ed25519:{alias_payload}"


@pytest.mark.unit
@pytest.mark.parametrize("alias_first", [False, True])
async def test_equivalent_public_key_encodings_share_one_canonical_identity(
    client,
    alias_first: bool,
) -> None:
    canonical_key, alias_key = _canonical_key_and_pad_bit_alias()
    first_key, second_key = (
        (alias_key, canonical_key) if alias_first else (canonical_key, alias_key)
    )

    first_response = await client.post(
        "/agents/register",
        json={"name": "First", "public_key": first_key},
    )
    second_response = await client.post(
        "/agents/register",
        json={"name": "Second", "public_key": second_key},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json()["error"] == "public_key_exists"

    agent_id = first_response.json()["agent_id"]
    stored_response = await client.get(f"/agents/{agent_id}")
    assert stored_response.status_code == 200
    assert stored_response.json()["public_key"] == canonical_key

    list_response = await client.get("/agents")
    assert list_response.status_code == 200
    assert len(list_response.json()["agents"]) == 1
