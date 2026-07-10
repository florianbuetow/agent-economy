"""Shared test helpers for JWS authentication and mocking."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from joserfc import jws
from joserfc.jwk import OKPKey

# Registry of signer public keys by ``kid``, populated by ``make_jws_token``. It lets the
# router-test verifiers perform faithful Ed25519 verification (accept genuinely-signed
# tokens, reject tampered ones) without a live Identity service or platform agent.
_PUBLIC_KEYS: dict[str, Ed25519PublicKey] = {}


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url JWS segment, restoring padding."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def verify_compact_jws(token: str) -> dict[str, Any]:
    """Faithfully verify a compact JWS against the registered key for its ``kid``.

    Returns the decoded payload; raises ``InvalidSignature`` if the token is malformed,
    the signer is unknown, or the signature does not match the signing input. Mirrors the
    contract of ``PlatformAgent.validate_certificate`` for use as a test double.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidSignature("malformed compact JWS")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidSignature("invalid JWS header") from exc
    kid = header.get("kid") if isinstance(header, dict) else None
    public_key = _PUBLIC_KEYS.get(kid) if isinstance(kid, str) else None
    if public_key is None:
        raise InvalidSignature("unknown signer")
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    public_key.verify(_b64url_decode(sig_b64), signing_input)
    decoded: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    return decoded


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
    _PUBLIC_KEYS[agent_id] = private_key.public_key()
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
    """Corrupt the signature of a JWS so faithful verification rejects it (403)."""
    parts = token.split(".")
    signature = bytearray(_b64url_decode(parts[2]))
    signature[0] ^= 0x01  # flip a bit so the Ed25519 signature no longer matches
    new_signature = base64.urlsafe_b64encode(bytes(signature)).rstrip(b"=").decode()
    return f"{parts[0]}.{parts[1]}.{new_signature}"
