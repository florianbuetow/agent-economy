"""Shared test helpers for JWS authentication and mocking."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from joserfc import jws
from joserfc.jwk import OKPKey


def generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Ed25519 keypair -> (private_key, 'ed25519:<base64_pub>')."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def make_jws_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    payload: dict[str, Any],
) -> str:
    """Create a real JWS compact token signed by the given key."""
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()
    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
    }
    key = OKPKey.import_key(jwk_dict)
    protected = {"alg": "EdDSA", "kid": agent_id}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return jws.serialize_compact(protected, payload_bytes, key, algorithms=["EdDSA"])


def make_fake_jws(payload: dict[str, Any], kid: str = "a-test-agent") -> str:
    """Build a structurally valid but unsigned JWS (for format-only tests)."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def tamper_jws(token: str) -> str:
    """Alter the payload of a JWS after signing (creates invalid signature)."""
    parts = token.split(".")
    payload_bytes = base64.urlsafe_b64decode(parts[1] + "==")
    payload = json.loads(payload_bytes)
    payload["_tampered"] = True
    new_payload = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{parts[0]}.{new_payload}.{parts[2]}"
