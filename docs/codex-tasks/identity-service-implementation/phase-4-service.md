# Phase 4 — Service Layer

## Working Directory

All paths relative to `services/identity/`.

---

## File 1: `src/identity_service/services/agent_registry.py`

Create this file. This is the core business logic — no FastAPI imports.

```python
"""Agent registry business logic."""

from __future__ import annotations

import base64
import sqlite3
import uuid
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from service_commons.exceptions import ServiceError


class AgentRegistry:
    """
    Manages agent registration, lookup, and signature verification.

    Uses SQLite for persistence with a UNIQUE constraint on public_key.
    """

    def __init__(
        self,
        db_path: str,
        algorithm: str,
        public_key_prefix: str,
        public_key_bytes: int,
        signature_bytes: int,
    ) -> None:
        self._algorithm = algorithm
        self._public_key_prefix = public_key_prefix
        self._public_key_bytes = public_key_bytes
        self._signature_bytes = signature_bytes
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the agents table if it doesn't exist."""
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT NOT NULL UNIQUE,
                registered_at TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def register_agent(self, name: str, public_key: str) -> dict[str, str]:
        """
        Register a new agent.

        Validates name and public key, then inserts into database.
        Returns the full agent record on success.

        Raises:
            ServiceError: INVALID_NAME, INVALID_PUBLIC_KEY, or PUBLIC_KEY_EXISTS
        """
        self._validate_name(name)
        self._validate_public_key(public_key)

        agent_id = f"a-{uuid.uuid4()}"
        registered_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

        try:
            self._db.execute(
                "INSERT INTO agents (agent_id, name, public_key, registered_at) VALUES (?, ?, ?, ?)",
                (agent_id, name, public_key, registered_at),
            )
            self._db.commit()
        except sqlite3.IntegrityError as exc:
            raise ServiceError(
                "PUBLIC_KEY_EXISTS",
                "This public key is already registered",
                409,
                {},
            ) from exc

        return {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
        }

    def verify_signature(
        self,
        agent_id: str,
        payload_b64: str,
        signature_b64: str,
    ) -> dict[str, object]:
        """
        Verify an Ed25519 signature for a given agent.

        Returns {"valid": True, "agent_id": ...} or {"valid": False, "reason": ...}.

        Raises:
            ServiceError: AGENT_NOT_FOUND, INVALID_BASE64, INVALID_SIGNATURE_LENGTH
        """
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ServiceError("AGENT_NOT_FOUND", "Agent not found", 404, {})

        # Decode base64 payload
        try:
            payload_bytes = base64.b64decode(payload_b64, validate=True)
        except Exception as exc:
            raise ServiceError(
                "INVALID_BASE64",
                "payload is not valid base64",
                400,
                {},
            ) from exc

        # Decode base64 signature
        try:
            sig_bytes = base64.b64decode(signature_b64, validate=True)
        except Exception as exc:
            raise ServiceError(
                "INVALID_BASE64",
                "signature is not valid base64",
                400,
                {},
            ) from exc

        # Validate signature length
        if len(sig_bytes) != self._signature_bytes:
            raise ServiceError(
                "INVALID_SIGNATURE_LENGTH",
                f"Signature must be exactly {self._signature_bytes} bytes",
                400,
                {},
            )

        # Extract raw public key bytes from stored key
        public_key_str: str = agent["public_key"]
        key_b64 = public_key_str.split(":", 1)[1]
        key_bytes = base64.b64decode(key_b64)

        # Verify Ed25519 signature
        try:
            public_key_obj = Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key_obj.verify(sig_bytes, payload_bytes)
            return {"valid": True, "agent_id": agent_id}
        except InvalidSignature:
            return {"valid": False, "reason": "signature mismatch"}

    def get_agent(self, agent_id: str) -> dict[str, str] | None:
        """
        Look up a single agent by ID.

        Returns the full agent record or None if not found.
        """
        cursor = self._db.execute(
            "SELECT agent_id, name, public_key, registered_at FROM agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row[0],
            "name": row[1],
            "public_key": row[2],
            "registered_at": row[3],
        }

    def list_agents(self) -> list[dict[str, str]]:
        """
        List all agents. Public keys are omitted for brevity.

        Returns list of agent summaries sorted by registration time.
        """
        cursor = self._db.execute(
            "SELECT agent_id, name, registered_at FROM agents ORDER BY registered_at"
        )
        return [
            {"agent_id": row[0], "name": row[1], "registered_at": row[2]}
            for row in cursor.fetchall()
        ]

    def count_agents(self) -> int:
        """Count total registered agents."""
        cursor = self._db.execute("SELECT COUNT(*) FROM agents")
        result = cursor.fetchone()
        if result is None:
            return 0
        return int(result[0])

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def _validate_name(self, name: str) -> None:
        """Validate agent display name."""
        if not name or not name.strip():
            raise ServiceError(
                "INVALID_NAME",
                "Name cannot be empty or whitespace-only",
                400,
                {},
            )

    def _validate_public_key(self, public_key: str) -> None:
        """
        Validate Ed25519 public key format and content.

        Expected format: "ed25519:<base64-encoded-32-bytes>"
        """
        if not public_key.startswith(self._public_key_prefix):
            raise ServiceError(
                "INVALID_PUBLIC_KEY",
                f"Public key must start with '{self._public_key_prefix}'",
                400,
                {},
            )

        key_b64 = public_key[len(self._public_key_prefix) :]

        try:
            key_bytes = base64.b64decode(key_b64, validate=True)
        except Exception as exc:
            raise ServiceError(
                "INVALID_PUBLIC_KEY",
                "Public key contains invalid base64",
                400,
                {},
            ) from exc

        if len(key_bytes) != self._public_key_bytes:
            raise ServiceError(
                "INVALID_PUBLIC_KEY",
                f"Public key must be exactly {self._public_key_bytes} bytes",
                400,
                {},
            )

        # Reject all-zero key (degenerate identity point)
        if key_bytes == b"\x00" * self._public_key_bytes:
            raise ServiceError(
                "INVALID_PUBLIC_KEY",
                "All-zero public key is not allowed",
                400,
                {},
            )

        # Validate key is a valid Ed25519 point
        try:
            Ed25519PublicKey.from_public_bytes(key_bytes)
        except Exception as exc:
            raise ServiceError(
                "INVALID_PUBLIC_KEY",
                "Not a valid Ed25519 public key",
                400,
                {},
            ) from exc
```

---

## File 2: `src/identity_service/services/__init__.py`

Overwrite the existing empty file with:

```python
"""Service layer components."""

from identity_service.services.agent_registry import AgentRegistry

__all__ = ["AgentRegistry"]
```

---

## Verification

```bash
cd services/identity && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
