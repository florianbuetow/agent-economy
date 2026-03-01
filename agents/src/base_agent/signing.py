"""
Ed25519 key management, JWS token creation, and verification.

Handles key generation, loading, and compact JWS (header.payload.signature)
token creation and verification using EdDSA for authenticating with platform services.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization

if TYPE_CHECKING:
    from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def generate_keypair(handle: str, keys_dir: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Generate a new Ed25519 keypair and persist to disk.

    Creates {handle}.key (private) and {handle}.pub (public) in PEM format.

    Args:
        handle: Agent handle used as filename prefix.
        keys_dir: Directory to write key files into.

    Returns:
        Tuple of (private_key, public_key).
    """
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_path = keys_dir / f"{handle}.key"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    public_path = keys_dir / f"{handle}.pub"
    public_path.write_bytes(
        public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    return private_key, public_key


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file.

    Args:
        path: Path to the PEM-encoded private key file.

    Returns:
        The loaded private key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 private key.
    """
    key_bytes = path.read_bytes()
    private_key = serialization.load_pem_private_key(key_bytes, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        msg = f"Expected Ed25519 private key, got {type(private_key).__name__}"
        raise ValueError(msg)
    return private_key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file.

    Args:
        path: Path to the PEM-encoded public key file.

    Returns:
        The loaded public key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 public key.
    """
    key_bytes = path.read_bytes()
    public_key = serialization.load_pem_public_key(key_bytes)
    if not isinstance(public_key, Ed25519PublicKey):
        msg = f"Expected Ed25519 public key, got {type(public_key).__name__}"
        raise ValueError(msg)
    return public_key


def public_key_to_b64(public_key: Ed25519PublicKey) -> str:
    """Export a public key as base64-encoded raw bytes.

    The Identity service expects keys in the format "ed25519:<base64>".
    This function returns only the base64 portion.

    Args:
        public_key: The Ed25519 public key to export.

    Returns:
        Base64-encoded string of the raw 32-byte public key.
    """
    raw_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_bytes).decode("ascii")


def _b64url_encode(data: bytes) -> str:
    """Base64url-encode bytes without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    """Base64url-decode a string, adding padding as needed."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)


def create_jws(
    payload: dict[str, object],
    private_key: Ed25519PrivateKey,
    kid: str | None = None,
) -> str:
    """Create a compact JWS token (header.payload.signature) using EdDSA.

    Produces a three-part dot-separated string:
    base64url(header).base64url(payload).base64url(signature).
    The header specifies alg=EdDSA. The signature covers "header.payload".

    Args:
        payload: Dictionary to sign as the JWS payload.
        private_key: Ed25519 private key used for signing.
        kid: Optional key ID (agent_id) to include in the JWS header.

    Returns:
        Compact JWS string.
    """
    header: dict[str, str] = {"alg": "EdDSA", "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = private_key.sign(signing_input)
    signature_b64 = _b64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def verify_jws(
    token: str,
    public_key: Ed25519PublicKey,
) -> dict[str, object]:
    """Verify a compact JWS token and return the decoded payload.

    Verifies the Ed25519 signature against the provided public key.
    Does NOT call any external service -- purely local cryptographic verification.

    Args:
        token: Compact JWS string (header.payload.signature).
        public_key: Ed25519 public key to verify against.

    Returns:
        Decoded payload as a dictionary.

    Raises:
        ValueError: If the token format is invalid.
        cryptography.exceptions.InvalidSignature: If the signature is invalid.
    """
    parts = token.split(".")
    if len(parts) != 3:
        msg = "Invalid JWS format: expected 3 dot-separated parts"
        raise ValueError(msg)

    header_b64, payload_b64, signature_b64 = parts
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = _b64url_decode(signature_b64)

    public_key.verify(signature, signing_input)

    payload_bytes = _b64url_decode(payload_b64)
    return json.loads(payload_bytes)
