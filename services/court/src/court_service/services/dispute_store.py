"""SQLite-backed dispute storage."""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, cast


class DuplicateDisputeError(Exception):
    """Raised when attempting to create a second dispute for the same task."""


class DisputeStore:
    """SQLite-backed dispute storage with thread-safe transactions."""

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
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS disputes (
                    dispute_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    claimant_id TEXT NOT NULL,
                    respondent_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    rebuttal TEXT,
                    status TEXT NOT NULL DEFAULT 'rebuttal_pending',
                    rebuttal_deadline TEXT NOT NULL,
                    worker_pct INTEGER,
                    ruling_summary TEXT,
                    escrow_id TEXT NOT NULL,
                    filed_at TEXT NOT NULL,
                    rebutted_at TEXT,
                    ruled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS votes (
                    vote_id TEXT PRIMARY KEY,
                    dispute_id TEXT NOT NULL REFERENCES disputes(dispute_id),
                    judge_id TEXT NOT NULL,
                    worker_pct INTEGER NOT NULL,
                    reasoning TEXT NOT NULL,
                    voted_at TEXT NOT NULL,
                    UNIQUE(dispute_id, judge_id)
                );
                """
            )
            self._db.commit()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _new_dispute_id() -> str:
        return f"disp-{uuid.uuid4()}"

    @staticmethod
    def _new_vote_id() -> str:
        return f"vote-{uuid.uuid4()}"

    def _row_to_dispute(self, row: sqlite3.Row, votes: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "dispute_id": str(row["dispute_id"]),
            "task_id": str(row["task_id"]),
            "claimant_id": str(row["claimant_id"]),
            "respondent_id": str(row["respondent_id"]),
            "claim": str(row["claim"]),
            "rebuttal": str(row["rebuttal"]) if row["rebuttal"] is not None else None,
            "status": str(row["status"]),
            "rebuttal_deadline": str(row["rebuttal_deadline"]),
            "worker_pct": int(row["worker_pct"]) if row["worker_pct"] is not None else None,
            "ruling_summary": (
                str(row["ruling_summary"]) if row["ruling_summary"] is not None else None
            ),
            "escrow_id": str(row["escrow_id"]),
            "filed_at": str(row["filed_at"]),
            "rebutted_at": str(row["rebutted_at"]) if row["rebutted_at"] is not None else None,
            "ruled_at": str(row["ruled_at"]) if row["ruled_at"] is not None else None,
            "votes": votes,
        }

    def get_dispute_row(self, dispute_id: str) -> sqlite3.Row | None:
        with self._lock:
            cursor = self._db.execute("SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,))
            return cast("sqlite3.Row | None", cursor.fetchone())

    def get_votes(self, dispute_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._db.execute(
                """
                SELECT vote_id, dispute_id, judge_id, worker_pct, reasoning, voted_at
                FROM votes
                WHERE dispute_id = ?
                ORDER BY voted_at, vote_id
                """,
                (dispute_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "vote_id": str(row["vote_id"]),
                "dispute_id": str(row["dispute_id"]),
                "judge_id": str(row["judge_id"]),
                "worker_pct": int(row["worker_pct"]),
                "reasoning": str(row["reasoning"]),
                "voted_at": str(row["voted_at"]),
            }
            for row in rows
        ]

    def insert_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline: str,
    ) -> dict[str, Any]:
        """Create a new dispute and return the created record."""
        dispute_id = self._new_dispute_id()
        filed_at = self._now_iso()

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    """
                    INSERT INTO disputes (
                        dispute_id, task_id, claimant_id, respondent_id, claim,
                        status, rebuttal_deadline, escrow_id, filed_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'rebuttal_pending', ?, ?, ?)
                    """,
                    (
                        dispute_id,
                        task_id,
                        claimant_id,
                        respondent_id,
                        claim,
                        rebuttal_deadline,
                        escrow_id,
                        filed_at,
                    ),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                error_msg = str(exc).lower()
                if "unique" in error_msg:
                    raise DuplicateDisputeError(
                        f"A dispute already exists for task_id={task_id}"
                    ) from exc
                raise
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

        dispute = self.get_dispute(dispute_id)
        if dispute is None:
            msg = "Failed to load newly created dispute"
            raise RuntimeError(msg)
        return dispute

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None:
        """Get dispute details including votes."""
        with self._lock:
            cursor = self._db.execute("SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            vote_cursor = self._db.execute(
                """
                SELECT vote_id, dispute_id, judge_id, worker_pct, reasoning, voted_at
                FROM votes
                WHERE dispute_id = ?
                ORDER BY voted_at, vote_id
                """,
                (dispute_id,),
            )
            vote_rows = vote_cursor.fetchall()

        votes = [
            {
                "vote_id": str(vote_row["vote_id"]),
                "dispute_id": str(vote_row["dispute_id"]),
                "judge_id": str(vote_row["judge_id"]),
                "worker_pct": int(vote_row["worker_pct"]),
                "reasoning": str(vote_row["reasoning"]),
                "voted_at": str(vote_row["voted_at"]),
            }
            for vote_row in vote_rows
        ]
        return self._row_to_dispute(cast("sqlite3.Row", row), votes)

    def update_rebuttal(self, dispute_id: str, rebuttal: str) -> None:
        """Persist rebuttal text and timestamp."""
        rebutted_at = self._now_iso()
        with self._lock:
            self._db.execute(
                "UPDATE disputes SET rebuttal = ?, rebutted_at = ? WHERE dispute_id = ?",
                (rebuttal, rebutted_at, dispute_id),
            )
            self._db.commit()

    def set_status(self, dispute_id: str, status: str) -> None:
        """Update dispute status."""
        with self._lock:
            self._db.execute(
                "UPDATE disputes SET status = ? WHERE dispute_id = ?",
                (status, dispute_id),
            )
            self._db.commit()

    def revert_to_rebuttal_pending(self, dispute_id: str) -> None:
        """Revert dispute status and remove any recorded votes."""
        with self._lock:
            self._db.execute(
                "UPDATE disputes SET status = 'rebuttal_pending' WHERE dispute_id = ?",
                (dispute_id,),
            )
            self._db.execute("DELETE FROM votes WHERE dispute_id = ?", (dispute_id,))
            self._db.commit()

    def persist_ruling(
        self,
        dispute_id: str,
        worker_pct: int,
        ruling_summary: str,
        votes: list[dict[str, Any]],
    ) -> None:
        """Persist ruling outcome and judge votes atomically."""
        ruled_at = self._now_iso()
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    """
                    UPDATE disputes
                    SET status = 'ruled', worker_pct = ?, ruling_summary = ?, ruled_at = ?
                    WHERE dispute_id = ?
                    """,
                    (worker_pct, ruling_summary, ruled_at, dispute_id),
                )
                for vote in votes:
                    self._db.execute(
                        """
                        INSERT INTO votes (
                            vote_id, dispute_id, judge_id, worker_pct, reasoning, voted_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            self._new_vote_id(),
                            dispute_id,
                            str(vote["judge_id"]),
                            int(vote["worker_pct"]),
                            str(vote["reasoning"]),
                            str(vote["voted_at"]),
                        ),
                    )
                self._db.commit()
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]:
        """List disputes with optional filters."""
        query = (
            "SELECT dispute_id, task_id, claimant_id, respondent_id, status, "
            "worker_pct, filed_at, ruled_at FROM disputes"
        )
        clauses: list[str] = []
        params: list[object] = []

        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        if len(clauses) > 0:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY filed_at"

        with self._lock:
            rows = self._db.execute(query, params).fetchall()

        return [
            {
                "dispute_id": str(row["dispute_id"]),
                "task_id": str(row["task_id"]),
                "claimant_id": str(row["claimant_id"]),
                "respondent_id": str(row["respondent_id"]),
                "status": str(row["status"]),
                "worker_pct": int(row["worker_pct"]) if row["worker_pct"] is not None else None,
                "filed_at": str(row["filed_at"]),
                "ruled_at": str(row["ruled_at"]) if row["ruled_at"] is not None else None,
            }
            for row in rows
        ]

    def count_disputes(self) -> int:
        """Count all disputes."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM disputes").fetchone()
        return int(row[0]) if row is not None else 0

    def count_active(self) -> int:
        """Count disputes that are not yet ruled."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM disputes WHERE status != 'ruled'"
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()
