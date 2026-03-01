#!/usr/bin/env python
"""JWS helper for acceptance tests."""

import base64
import json
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jws
from joserfc.jwk import OKPKey

USAGE = (
    "Usage:\n"
    "  jws_helper.py keygen\n"
    "  jws_helper.py jws <private_key_hex> <agent_id> <json_payload>"
)


def keygen() -> None:
    """Generate Ed25519 keypair."""
    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes_raw()
    public_raw = private_key.public_key().public_bytes_raw()
    print(private_raw.hex())
    print(f"ed25519:{base64.b64encode(public_raw).decode()}")


def make_jws(private_hex: str, agent_id: str, payload_json: str) -> None:
    """Create a JWS compact token."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
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
    # Normalize JSON: compact, sorted keys
    payload_bytes = json.dumps(
        json.loads(payload_json), separators=(",", ":"), sort_keys=True
    ).encode()
    token = jws.serialize_compact(protected, payload_bytes, key, algorithms=["EdDSA"])
    print(token)


def main() -> None:
    if len(sys.argv) < 2:
        print(USAGE, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "keygen" and len(sys.argv) == 2:
        keygen()
    elif cmd == "jws" and len(sys.argv) == 5:
        make_jws(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print(USAGE, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
