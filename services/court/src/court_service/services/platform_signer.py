"""Platform JWS signer for outgoing inter-service calls."""

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
    """Create EdDSA compact JWS tokens with the platform private key."""

    def __init__(self, private_key_path: str, platform_agent_id: str) -> None:
        self._agent_id = platform_agent_id

        key_data = Path(private_key_path).read_bytes()

        private_key: Ed25519PrivateKey
        try:
            loaded = load_pem_private_key(key_data, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                msg = "Platform private key must be an Ed25519 private key"
                raise ValueError(msg)
            private_key = loaded
        except ValueError:
            private_key = Ed25519PrivateKey.from_private_bytes(key_data)

        raw_private = private_key.private_bytes_raw()
        raw_public = private_key.public_key().public_bytes_raw()

        jwk_dict: dict[str, str | list[str]] = {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
        }
        self._key = OKPKey.import_key(jwk_dict)

    def sign(self, payload: dict[str, Any]) -> str:
        """Sign payload and return compact JWS token."""
        protected = {"alg": "EdDSA", "kid": self._agent_id}
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        return jws.serialize_compact(protected, payload_bytes, self._key, algorithms=["EdDSA"])
