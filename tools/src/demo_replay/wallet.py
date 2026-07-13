"""Ed25519 key management and JWS token creation for demo agents.

Signing and canonicalization come from the shared ``service_auth`` library
(WP-02) so demo tokens are byte-compatible with every other signer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from service_auth.signing import create_jws, load_private_key, public_key_to_b64

if TYPE_CHECKING:
    from pathlib import Path


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
        private_key = Ed25519PrivateKey.generate()
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
        )

    @classmethod
    def from_pem(cls, handle: str, name: str, private_key_path: Path) -> DemoAgent:
        """Load a demo agent from existing PEM key files on disk."""
        private_key = load_private_key(private_key_path)
        return cls(
            handle=handle,
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
        )

    def public_key_string(self) -> str:
        """Return 'ed25519:<base64>' format expected by Identity service."""
        return f"ed25519:{public_key_to_b64(self.public_key)}"

    def sign_jws(self, payload: dict[str, object]) -> str:
        """Sign a payload as a compact JWS token."""
        if self.agent_id is None:
            msg = f"Agent '{self.handle}' must be registered before signing"
            raise RuntimeError(msg)
        return create_jws(payload, self.private_key, kid=self.agent_id)

    def auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization: Bearer header with a signed JWS."""
        return {"Authorization": f"Bearer {self.sign_jws(payload)}"}
