"""SQLite-backed feedback storage."""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from reputation_service.core.state import FeedbackRecord


class DuplicateFeedbackError(Exception):
    """Raised when a duplicate (task_id, from_agent_id, to_agent_id) is inserted."""


class FeedbackStore:
    """
    SQLite-backed feedback storage with thread-safe transactions.

    Replaces the in-memory FeedbackStore dataclass. Owns the SQLite
    connection, sets pragmas, creates the schema, and exposes operations
    for insert, lookup, and count.
    """

    def __init__(self, db_path: str) -> None:
        self._lock = RLock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables and indexes if they don't exist."""
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    feedback_id    TEXT PRIMARY KEY,
                    task_id        TEXT NOT NULL,
                    from_agent_id  TEXT NOT NULL,
                    to_agent_id    TEXT NOT NULL,
                    category       TEXT NOT NULL,
                    rating         TEXT NOT NULL,
                    comment        TEXT,
                    submitted_at   TEXT NOT NULL,
                    visible        INTEGER NOT NULL DEFAULT 0
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_feedback_pair
                    ON feedback (task_id, from_agent_id, to_agent_id);

                CREATE INDEX IF NOT EXISTS ix_feedback_task
                    ON feedback (task_id);

                CREATE INDEX IF NOT EXISTS ix_feedback_target_agent
                    ON feedback (to_agent_id);
                """
            )
            self._db.commit()

    def _row_to_record(self, row: sqlite3.Row) -> FeedbackRecord:
        """Convert a database row to a FeedbackRecord."""
        return FeedbackRecord(
            feedback_id=str(row["feedback_id"]),
            task_id=str(row["task_id"]),
            from_agent_id=str(row["from_agent_id"]),
            to_agent_id=str(row["to_agent_id"]),
            category=str(row["category"]),
            rating=str(row["rating"]),
            comment=str(row["comment"]) if row["comment"] is not None else None,
            submitted_at=str(row["submitted_at"]),
            visible=bool(row["visible"]),
        )

    def insert_feedback(
        self,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
        category: str,
        rating: str,
        comment: str | None,
    ) -> FeedbackRecord:
        """
        Insert a feedback record and handle mutual reveal atomically.

        Raises:
            DuplicateFeedbackError: If the (task_id, from_agent_id, to_agent_id)
                triple already exists.
        """
        feedback_id = f"fb-{uuid.uuid4()}"
        submitted_at = datetime.now(UTC).isoformat()

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")

                # Insert the new feedback record (sealed by default)
                self._db.execute(
                    """
                    INSERT INTO feedback
                        (feedback_id, task_id, from_agent_id, to_agent_id,
                         category, rating, comment, submitted_at, visible)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        feedback_id,
                        task_id,
                        from_agent_id,
                        to_agent_id,
                        category,
                        rating,
                        comment,
                        submitted_at,
                    ),
                )

                # Check for reverse pair
                cursor = self._db.execute(
                    """
                    SELECT feedback_id FROM feedback
                    WHERE task_id = ? AND from_agent_id = ? AND to_agent_id = ?
                    """,
                    (task_id, to_agent_id, from_agent_id),
                )
                reverse_row = cursor.fetchone()

                visible = False
                if reverse_row is not None:
                    # Mutual reveal: set both records visible
                    reverse_feedback_id = str(reverse_row["feedback_id"])
                    self._db.execute(
                        "UPDATE feedback SET visible = 1 WHERE feedback_id IN (?, ?)",
                        (feedback_id, reverse_feedback_id),
                    )
                    visible = True

                self._db.commit()

            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                error_msg = str(exc).lower()
                if "unique" in error_msg:
                    raise DuplicateFeedbackError(
                        f"Feedback already exists for ({task_id}, {from_agent_id}, {to_agent_id})"
                    ) from exc
                raise
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

        return FeedbackRecord(
            feedback_id=feedback_id,
            task_id=task_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            category=category,
            rating=rating,
            comment=comment,
            submitted_at=submitted_at,
            visible=visible,
        )

    def get_by_id(self, feedback_id: str) -> FeedbackRecord | None:
        """Get a feedback record by ID. Returns None if not found."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM feedback WHERE feedback_id = ?",
                (feedback_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_task(self, task_id: str) -> list[FeedbackRecord]:
        """Get all feedback records for a task, ordered by submitted_at."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM feedback WHERE task_id = ? ORDER BY submitted_at",
                (task_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_by_agent(self, agent_id: str) -> list[FeedbackRecord]:
        """Get all feedback records about an agent (to_agent_id), ordered by submitted_at."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT * FROM feedback WHERE to_agent_id = ? ORDER BY submitted_at",
                (agent_id,),
            )
            rows = cursor.fetchall()
        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        """Count total feedback records (including sealed)."""
        with self._lock:
            cursor = self._db.execute("SELECT COUNT(*) FROM feedback")
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()
