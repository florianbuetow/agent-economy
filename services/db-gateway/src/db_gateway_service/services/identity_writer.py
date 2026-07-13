"""Identity domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. Reads the shared single-writer SQLite connection
through DbWriter's public ``connection`` property (never a private attribute
— see tests/architecture/test_gap_c5_single_writer_invariant.py) and the
shared transaction helpers from db_writer_helpers.py. Every method here stays
a plain (non-async) ``def`` that runs BEGIN IMMEDIATE through COMMIT/ROLLBACK
to completion without awaiting anything in between, preserving the same
single-writer invariant db_writer.py's methods upheld before the split.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer_helpers import (
    insert_event,
    is_foreign_key_violation,
    lookup_registration_event_id,
)

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


class IdentityWriter:
    """Handles agent registration writes."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    def register_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Register a new agent.

        INSERT INTO identity_agents + INSERT INTO events.
        Idempotency: UNIQUE on public_key.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
                "VALUES (?, ?, ?, ?)",
                (data["agent_id"], data["name"], data["public_key"], data["registered_at"]),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"agent_id": data["agent_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            # identity_agents has exactly one non-PK UNIQUE constraint (public_key),
            # so SQLITE_CONSTRAINT_UNIQUE unambiguously means a public_key collision.
            if exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                # Check for idempotent replay
                existing = self._lookup_agent_by_public_key(data["public_key"])
                if existing is not None and self._agent_matches(existing, data):
                    return {
                        "agent_id": existing["agent_id"],
                        "event_id": lookup_registration_event_id(
                            db,
                            existing["agent_id"],
                            data["event"]["event_source"],
                            data["event"]["event_type"],
                        ),
                    }
                raise ServiceError(
                    "public_key_exists",
                    "This public key is already registered",
                    409,
                    {},
                ) from exc
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "public_key_exists",
                "This public key is already registered",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def _lookup_agent_by_public_key(self, public_key: str) -> dict[str, str] | None:
        """Look up an agent by public key."""
        cursor = self._writer.connection.execute(
            "SELECT agent_id, name, public_key, registered_at "
            "FROM identity_agents WHERE public_key = ?",
            (public_key,),
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

    def _agent_matches(self, existing: dict[str, str], data: dict[str, Any]) -> bool:
        """Check if all agent fields match for idempotency."""
        name_match: bool = existing["name"] == data["name"]
        time_match: bool = existing["registered_at"] == data["registered_at"]
        return name_match and time_match
