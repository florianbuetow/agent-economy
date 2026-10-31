"""Agent registry business logic."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from service_auth.jws import JwsAdmissionError, TokenPolicy, parse_jws, verify_jws
from service_commons.exceptions import ServiceError

from identity_service.services.errors import DuplicateAgentError

if TYPE_CHECKING:
    from pathlib import Path

    from identity_service.services.protocol import IdentityStorageInterface


class AgentRegistry:
    """
    Manages agent registration, lookup, and signature verification.
    """

    def __init__(
        self,
        store: IdentityStorageInterface,
        public_key_prefix: str,
        public_key_bytes: int,
        signature_bytes: int,
        token_policy: TokenPolicy,
        reserved_names: frozenset[str],
        trusted_key_files: tuple[Path, ...],
    ) -> None:
        self._store = store
        self._public_key_prefix = public_key_prefix
        self._public_key_bytes = public_key_bytes
        self._signature_bytes = signature_bytes
        self._token_policy = token_policy
        self._reserved_names = reserved_names
        self._trusted_key_files = trusted_key_files

    async def register_agent(self, name: str, public_key: str) -> dict[str, str]:
        """
        Register a new agent.

        Validates name and public key, then inserts into database.
        Returns the full agent record on success.

        Raises:
            ServiceError: INVALID_NAME, INVALID_PUBLIC_KEY, or PUBLIC_KEY_EXISTS
        """
        self._validate_name(name)
        canonical_public_key = self._validate_public_key(public_key)
        self._check_reserved_name(name, canonical_public_key)

        try:
            return await self._store.insert(name, canonical_public_key)
        except DuplicateAgentError as exc:
            raise ServiceError(
                "public_key_exists",
                "This public key is already registered",
                409,
                {},
            ) from exc

    async def verify_jws(self, token: str) -> dict[str, object]:
        """
        Verify a JWS compact token and return the payload.

        Extracts the kid (agent_id) from the protected header, looks up the
        agent's public key, and verifies the EdDSA signature.

        Returns {"valid": True, "agent_id": "...", "payload": {...}} on success,
        or {"valid": False, "reason": "..."} on signature mismatch.

        Raises:
            ServiceError: INVALID_JWS if token is malformed, missing kid,
                wrong algorithm, or payload is not valid JSON.
            ServiceError: AGENT_NOT_FOUND if kid references unknown agent.
        """
        try:
            parsed = parse_jws(token)
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

        # Look up agent
        agent = await self.get_agent(parsed.header.kid)
        if agent is None:
            raise ServiceError("agent_not_found", "Agent not found", 404, {})

        # Extract raw public key bytes
        public_key_str: str = agent["public_key"]
        key_b64 = public_key_str.split(":", 1)[1]
        raw_public = base64.b64decode(key_b64)

        try:
            verified = verify_jws(
                token,
                Ed25519PublicKey.from_public_bytes(raw_public),
                policy=self._token_policy,
            )
        except InvalidSignature:
            return {"valid": False, "reason": "signature mismatch"}
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

        return {
            "valid": True,
            "agent_id": verified.header.kid,
            "protected_header": vars(verified.header),
            "payload": verified.payload,
        }

    async def get_agent(self, agent_id: str) -> dict[str, str] | None:
        """
        Look up a single agent by ID.

        Returns the full agent record or None if not found.
        """
        return await self._store.get_by_id(agent_id)

    async def list_agents(self) -> list[dict[str, str]]:
        """
        List all agents. Public keys are omitted for brevity.

        Returns list of agent summaries sorted by registration time.
        """
        return await self._store.list_all()

    async def count_agents(self) -> int:
        """Count total registered agents."""
        return await self._store.count()

    async def close(self) -> None:
        """Close the database connection."""
        await self._store.close()

    def _check_reserved_name(self, name: str, canonical_public_key: str) -> None:
        """Reject reserved display names unless the key is a pinned platform key."""
        if name.strip().casefold() not in self._reserved_names:
            return
        if canonical_public_key in self._trusted_public_keys():
            return
        raise ServiceError(
            "reserved_name",
            "This display name is reserved",
            409,
            {},
        )

    def _trusted_public_keys(self) -> set[str]:
        """Read the pinned PEM public keys; unreadable or absent files pin nothing."""
        keys: set[str] = set()
        for path in self._trusted_key_files:
            try:
                loaded = serialization.load_pem_public_key(path.read_bytes())
            except (OSError, ValueError):
                continue
            if not isinstance(loaded, Ed25519PublicKey):
                continue
            raw = loaded.public_bytes(Encoding.Raw, PublicFormat.Raw)
            keys.add(f"{self._public_key_prefix}{base64.b64encode(raw).decode()}")
        return keys

    def _validate_name(self, name: str) -> None:
        """Validate agent display name."""
        if not name or not name.strip():
            raise ServiceError(
                "invalid_name",
                "Name cannot be empty or whitespace-only",
                400,
                {},
            )

    def _validate_public_key(self, public_key: str) -> str:
        """
        Validate Ed25519 public key format and content.

        Expected format: "ed25519:<base64-encoded-32-bytes>"
        """
        if not public_key.startswith(self._public_key_prefix):
            raise ServiceError(
                "invalid_public_key",
                f"Public key must start with '{self._public_key_prefix}'",
                400,
                {},
            )

        key_b64 = public_key[len(self._public_key_prefix) :]

        try:
            key_bytes = base64.b64decode(key_b64, validate=True)
        except Exception as exc:
            raise ServiceError(
                "invalid_public_key",
                "Public key contains invalid base64",
                400,
                {},
            ) from exc

        if len(key_bytes) != self._public_key_bytes:
            raise ServiceError(
                "invalid_public_key",
                f"Public key must be exactly {self._public_key_bytes} bytes",
                400,
                {},
            )

        # Reject all-zero key (degenerate identity point)
        if key_bytes == b"\x00" * self._public_key_bytes:
            raise ServiceError(
                "invalid_public_key",
                "All-zero public key is not allowed",
                400,
                {},
            )

        # Validate key is a valid Ed25519 point
        try:
            Ed25519PublicKey.from_public_bytes(key_bytes)
        except Exception as exc:
            raise ServiceError(
                "invalid_public_key",
                "Not a valid Ed25519 public key",
                400,
                {},
            ) from exc

        canonical_payload = base64.b64encode(key_bytes).decode("ascii")
        return f"{self._public_key_prefix}{canonical_payload}"
