"""Platform JWS token signer for outgoing escrow operations."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from joserfc import jws
from joserfc.jwk import OKPKey


class PlatformSigner:
    """
    Creates JWS compact tokens signed with the platform agent's Ed25519 private key.

    Used for outgoing calls to the Central Bank where the Task Board acts as
    the platform agent (escrow release on approval, cancellation, expiration).

    The platform agent must be registered with the Identity service, and the
    corresponding public key must be stored there. The Central Bank verifies
    the platform-signed token via the Identity service.
    """

    def __init__(self, platform_agent_id: str, private_key_path: str) -> None:
        self._agent_id = platform_agent_id

        # Load Ed25519 private key from PEM file
        pem_data = Path(private_key_path).read_bytes()
        private_key = load_pem_private_key(pem_data, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            msg = "Platform private key must be an Ed25519 private key"
            raise ValueError(msg)

        # Extract raw key bytes for JWK construction
        raw_private = private_key.private_bytes_raw()
        raw_public = private_key.public_key().public_bytes_raw()

        # Build OKP JWK for joserfc
        jwk_dict: dict[str, str | list[str]] = {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
        }
        self._key = OKPKey.import_key(jwk_dict)

    def sign(self, payload: dict[str, Any]) -> str:
        """
        Create a JWS compact serialization token.

        Args:
            payload: The JWS payload as a dict. Must include an "action" field
                    (e.g., "escrow_release").

        Returns:
            JWS compact serialization string (header.payload.signature)
        """
        protected = {"alg": "EdDSA", "kid": self._agent_id}
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return jws.serialize_compact(protected, payload_bytes, self._key, algorithms=["EdDSA"])
