"""Compatibility shim — Ed25519 signing/JWS moved to ``service_auth`` (WP-02).

Re-exports the signing API from ``libs/service-auth`` so existing
``base_agent.signing`` imports keep working unchanged.
"""

from service_auth.signing import (
    PlatformSigner,
    TokenExpiredError,
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
    verify_jws,
)

__all__ = [
    "PlatformSigner",
    "TokenExpiredError",
    "create_jws",
    "generate_keypair",
    "load_private_key",
    "load_public_key",
    "public_key_to_b64",
    "verify_jws",
]
