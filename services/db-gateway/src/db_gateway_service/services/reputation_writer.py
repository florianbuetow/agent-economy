"""Reputation domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. See identity_writer.py's module docstring for
the shared-connection/no-await invariant this preserves.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer_helpers import insert_event, is_foreign_key_violation

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter


class ReputationWriter:
    """Handles feedback submission and sealed-pair reveal writes."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    def _lookup_reverse_feedback_id(
        self,
        cursor: sqlite3.Cursor,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
    ) -> str | None:
        """Find the counter-feedback: the same task, with the two agents swapped."""
        cursor.execute(
            "SELECT feedback_id FROM reputation_feedback "
            "WHERE task_id = ? AND from_agent_id = ? AND to_agent_id = ?",
            (task_id, to_agent_id, from_agent_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return str(row["feedback_id"])

    def _lookup_agent_names(
        self,
        cursor: sqlite3.Cursor,
        agent_ids: tuple[str, str],
    ) -> dict[str, str]:
        """Resolve agent display names. Both ids are FK-guaranteed to exist."""
        cursor.execute(
            "SELECT agent_id, name FROM identity_agents WHERE agent_id IN (?, ?)",
            agent_ids,
        )
        return {str(row["agent_id"]): str(row["name"]) for row in cursor.fetchall()}

    def _build_reveal_event(
        self,
        cursor: sqlite3.Cursor,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the feedback.revealed event for a pair that just became visible."""
        from_agent_id = str(data["from_agent_id"])
        to_agent_id = str(data["to_agent_id"])
        names = self._lookup_agent_names(cursor, (from_agent_id, to_agent_id))
        from_name = names[from_agent_id]
        to_name = names[to_agent_id]
        return {
            "event_source": "reputation",
            "event_type": "feedback.revealed",
            "timestamp": data["submitted_at"],
            "task_id": data["task_id"],
            "agent_id": from_agent_id,
            "summary": f"Sealed feedback revealed between {from_name} and {to_name}",
            "payload": json.dumps(
                {
                    "task_id": data["task_id"],
                    "from_name": from_name,
                    "to_name": to_name,
                    "category": data["category"],
                }
            ),
        }

    def submit_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Submit feedback, revealing the sealed pair atomically.

        INSERT INTO reputation_feedback + reverse-pair lookup + UPDATE of both rows
        + INSERT INTO events, all inside one BEGIN IMMEDIATE transaction.

        The reveal policy lives here rather than in the caller. A caller that reads
        the reverse pair before writing races with the counter-feedback: both readers
        see "no reverse yet" and both rows stay sealed forever. Deciding inside the
        write transaction makes the both-sealed outcome unreachable.

        ``force_visible`` bypasses sealing for platform-submitted ruling feedback.
        """
        db = self._writer.connection
        force_visible = bool(data.get("force_visible", False))
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO reputation_feedback "
                "(feedback_id, task_id, from_agent_id, to_agent_id, role, "
                "category, rating, comment, submitted_at, visible) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["feedback_id"],
                    data["task_id"],
                    data["from_agent_id"],
                    data["to_agent_id"],
                    data["role"],
                    data["category"],
                    data["rating"],
                    data.get("comment"),
                    data["submitted_at"],
                    1 if force_visible else 0,
                ),
            )
            # The reverse lookup is a fact about the database, not a caller policy:
            # it runs even under force_visible, so a sealed counterpart is still revealed.
            reverse_id = self._lookup_reverse_feedback_id(
                cursor,
                str(data["task_id"]),
                str(data["from_agent_id"]),
                str(data["to_agent_id"]),
            )
            revealed = reverse_id is not None
            if revealed:
                cursor.execute(
                    "UPDATE reputation_feedback SET visible = 1 WHERE feedback_id IN (?, ?)",
                    (data["feedback_id"], reverse_id),
                )
            event_id = insert_event(cursor, data["event"])
            if revealed:
                insert_event(cursor, self._build_reveal_event(cursor, data))
            db.commit()
            return {
                "feedback_id": data["feedback_id"],
                "visible": force_visible or revealed,
                "event_id": event_id,
            }
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "feedback_exists",
                "Feedback already submitted for this (task, from, to) triple",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise
