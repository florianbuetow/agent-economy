"""Shared test helpers for JWS authentication and mocking."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth.jws import InvalidPayloadError, VerifiedJws, create_jws, parse_jws

# Registry of signer public keys by ``kid``, populated by ``make_jws_token``. It lets the
# router-test verifiers perform faithful Ed25519 verification (accept genuinely-signed
# tokens, reject tampered ones) without a live Identity service or platform agent.
_PUBLIC_KEYS: dict[str, Ed25519PublicKey] = {}


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url JWS segment, restoring padding."""
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def verify_compact_jws(token: str) -> VerifiedJws:
    """Faithfully verify a compact JWS against the registered key for its ``kid``.

    Returns the decoded payload; raises ``InvalidSignature`` if the token is malformed,
    the signer is unknown, or the signature does not match the signing input. Mirrors the
    contract of ``PlatformAgent.validate_certificate`` for use as a test double.
    """
    parsed = parse_jws(token)
    public_key = _PUBLIC_KEYS.get(parsed.header.kid)
    if public_key is None:
        raise InvalidSignature("unknown signer")
    public_key.verify(parsed.signature, parsed.signing_input)
    action = parsed.payload.get("action")
    if not isinstance(action, str) or not action:
        raise InvalidPayloadError("Invalid JWS payload action")
    return VerifiedJws(header=parsed.header, payload=parsed.payload)


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
    *,
    audience: str = "task-board",
    resource: str | None = None,
) -> str:
    """Create a real JWS compact token signed by the given key."""
    _PUBLIC_KEYS[agent_id] = private_key.public_key()
    return create_jws(
        payload,
        private_key,
        kid=agent_id,
        audience=audience,
        resource=resource or _default_resource(payload),
        token_ttl_seconds=60,
    )


def _default_resource(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    task_id = payload.get("task_id")
    resources = {
        "create_task": "POST /tasks",
        "submit_bid": f"POST /tasks/{task_id}/bids",
        "list_bids": f"GET /tasks/{task_id}/bids",
        "accept_bid": f"POST /tasks/{task_id}/bids/{payload.get('bid_id')}/accept",
        "upload_asset": f"POST /tasks/{task_id}/assets",
        "submit_deliverable": f"POST /tasks/{task_id}/submit",
        "approve_task": f"POST /tasks/{task_id}/approve",
        "dispute_task": f"POST /tasks/{task_id}/dispute",
        "submit_rebuttal": f"POST /tasks/{task_id}/rebuttal",
        "cancel_task": f"POST /tasks/{task_id}/cancel",
        "record_ruling": f"POST /tasks/{task_id}/ruling",
        "escrow_lock": "POST /escrow/lock",
    }
    resource = resources.get(action)
    if resource is None or "None" in resource:
        raise ValueError(f"Test token requires explicit resource for action {action!r}")
    return resource


def make_profile_jws_with_unchecked_payload(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    payload: dict[str, Any],
    *,
    resource: str,
    audience: str = "task-board",
) -> str:
    """Sign a malformed payload under an otherwise strict protected header."""
    issued_at = int(datetime.now(UTC).timestamp())
    header = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": agent_id,
        "iat": issued_at,
        "exp": issued_at + 60,
        "aud": audience,
        "resource": resource,
    }
    header_segment = _b64url_encode(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    signing_input = f"{header_segment}.{payload_segment}".encode()
    _PUBLIC_KEYS[agent_id] = private_key.public_key()
    return f"{header_segment}.{payload_segment}.{_b64url_encode(private_key.sign(signing_input))}"


def make_fake_jws(
    payload: dict[str, Any],
    kid: str = "a-test-agent",
    *,
    resource: str | None = None,
) -> str:
    """Build a structurally valid but unsigned JWS (for format-only tests)."""
    return tamper_jws(
        make_jws_token(
            Ed25519PrivateKey.generate(),
            kid,
            payload,
            resource=resource,
        )
    )


def tamper_jws(token: str) -> str:
    """Corrupt the signature of a JWS so faithful verification rejects it (403)."""
    parts = token.split(".")
    signature = bytearray(_b64url_decode(parts[2]))
    signature[0] ^= 0x01  # flip a bit so the Ed25519 signature no longer matches
    new_signature = base64.urlsafe_b64encode(bytes(signature)).rstrip(b"=").decode()
    return f"{parts[0]}.{parts[1]}.{new_signature}"
