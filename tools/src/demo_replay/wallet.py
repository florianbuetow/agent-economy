"""Ed25519 key management and JWS token creation for demo agents.

Extracted from agents/src/base_agent/signing.py to avoid pulling in
heavy agent dependencies (strands-agents, openai).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a fresh Ed25519 keypair (in-memory only, no disk persistence)."""
    private_key = Ed25519PrivateKey.generate()
    return private_key, private_key.public_key()


def _public_key_b64(public_key: Ed25519PublicKey) -> str:
    """Export public key as base64 (standard, not URL-safe) of raw 32 bytes."""
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def create_jws(
    payload: dict[str, object],
    private_key: Ed25519PrivateKey,
    kid: str,
) -> str:
    """Create a compact JWS token: base64url(header).base64url(payload).base64url(sig)."""
    header: dict[str, str] = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    sig_b64 = _b64url_encode(signature)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


@dataclass
class DemoAgent:
    """In-memory agent with keypair, used during demo replay."""

    handle: str
    name: str
    private_key: Ed25519PrivateKey = field(repr=False)
    public_key: Ed25519PublicKey = field(repr=False)
    agent_id: str | None = None

    @classmethod
    def create(cls, handle: str, name: str) -> DemoAgent:
        """Create a new demo agent with a fresh keypair."""
        private_key, public_key = _generate_keypair()
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=public_key,
        )

    @classmethod
    def from_pem(cls, handle: str, name: str, private_key_path: Path) -> DemoAgent:
        """Load a demo agent from existing PEM key files on disk."""
        key_bytes = private_key_path.read_bytes()
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            msg = f"Expected Ed25519 private key, got {type(private_key).__name__}"
            raise ValueError(msg)
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
        )

    def public_key_string(self) -> str:
        """Return 'ed25519:<base64>' format expected by Identity service."""
        return f"ed25519:{_public_key_b64(self.public_key)}"

    def sign_jws(self, payload: dict[str, object]) -> str:
        """Sign a payload as a compact JWS token."""
        if self.agent_id is None:
            msg = f"Agent '{self.handle}' must be registered before signing"
            raise RuntimeError(msg)
        return create_jws(payload, self.private_key, kid=self.agent_id)

    def auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization: Bearer header with a signed JWS."""
        return {"Authorization": f"Bearer {self.sign_jws(payload)}"}
