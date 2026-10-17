"""Compatibility shim — Ed25519 signing/JWS moved to ``service_auth`` (WP-02).

Re-exports the signing API from ``libs/service-auth`` so existing
``base_agent.signing`` imports keep working unchanged.
"""

from service_auth.jws import (
    JwsProtectedHeader,
    ParsedJws,
    TokenNotYetValidError,
    TokenPolicy,
    VerifiedJws,
    parse_identity_verification_response,
    parse_jws,
    require_context,
    require_signer,
)
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
    "JwsProtectedHeader",
    "ParsedJws",
    "PlatformSigner",
    "TokenExpiredError",
    "TokenNotYetValidError",
    "TokenPolicy",
    "VerifiedJws",
    "create_jws",
    "generate_keypair",
    "load_private_key",
    "load_public_key",
    "parse_identity_verification_response",
    "parse_jws",
    "public_key_to_b64",
    "require_context",
    "require_signer",
    "verify_jws",
]
