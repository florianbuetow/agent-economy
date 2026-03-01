"""SQLite-backed task storage."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


class DuplicateTaskError(Exception):
    """Raised when attempting to insert a task with a duplicate task_id."""


class DuplicateBidError(Exception):
    """Raised when attempting to insert a duplicate bid for a task/bidder pair."""


class TaskStore:
    """SQLite-backed storage for tasks, bids, and assets."""

    _TASK_COLUMNS: tuple[str, ...] = (
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "status",
        "escrow_id",
        "bid_count",
        "worker_id",
        "accepted_bid_id",
        "created_at",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "dispute_reason",
        "ruling_id",
        "ruled_at",
        "worker_pct",
        "ruling_summary",
        "expired_at",
        "escrow_pending",
    )
    _TASK_COLUMNS_SQL = (
        "task_id, poster_id, title, spec, reward, bidding_deadline_seconds, deadline_seconds, "
        "review_deadline_seconds, status, escrow_id, bid_count, worker_id, accepted_bid_id, "
        "created_at, accepted_at, submitted_at, approved_at, cancelled_at, disputed_at, "
        "dispute_reason, ruling_id, ruled_at, worker_pct, ruling_summary, expired_at, "
        "escrow_pending"
    )
    _TASK_INSERT_SQL = (
        "INSERT INTO tasks ("
        "task_id, poster_id, title, spec, reward, bidding_deadline_seconds, deadline_seconds, "
        "review_deadline_seconds, status, escrow_id, bid_count, worker_id, accepted_bid_id, "
        "created_at, accepted_at, submitted_at, approved_at, cancelled_at, disputed_at, "
        "dispute_reason, ruling_id, ruled_at, worker_pct, ruling_summary, expired_at, "
        "escrow_pending"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    _TASK_SELECT_BY_ID_SQL = (
        "SELECT task_id, poster_id, title, spec, reward, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, status, escrow_id, bid_count, "
        "worker_id, accepted_bid_id, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, dispute_reason, ruling_id, ruled_at, worker_pct, "
        "ruling_summary, expired_at, escrow_pending FROM tasks WHERE task_id = ?"
    )
    _TASK_SELECT_BASE_SQL = (
        "SELECT task_id, poster_id, title, spec, reward, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, status, escrow_id, bid_count, "
        "worker_id, accepted_bid_id, created_at, accepted_at, submitted_at, approved_at, "
        "cancelled_at, disputed_at, dispute_reason, ruling_id, ruled_at, worker_pct, "
        "ruling_summary, expired_at, escrow_pending FROM tasks"
    )

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
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    poster_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    spec TEXT NOT NULL,
                    reward INTEGER NOT NULL,
                    bidding_deadline_seconds INTEGER NOT NULL,
                    deadline_seconds INTEGER NOT NULL,
                    review_deadline_seconds INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    escrow_id TEXT NOT NULL,
                    bid_count INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    accepted_bid_id TEXT,
                    created_at TEXT NOT NULL,
                    accepted_at TEXT,
                    submitted_at TEXT,
                    approved_at TEXT,
                    cancelled_at TEXT,
                    disputed_at TEXT,
                    dispute_reason TEXT,
                    ruling_id TEXT,
                    ruled_at TEXT,
                    worker_pct INTEGER,
                    ruling_summary TEXT,
                    expired_at TEXT,
                    escrow_pending INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS bids (
                    bid_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    bidder_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    submitted_at TEXT NOT NULL,
                    UNIQUE(task_id, bidder_id)
                );

                CREATE TABLE IF NOT EXISTS assets (
                    asset_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    uploader_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    uploaded_at TEXT NOT NULL
                );
                """
            )
            self._db.commit()

    def _row_to_task(self, row: sqlite3.Row) -> dict[str, Any]:
        return {column: row[column] for column in self._TASK_COLUMNS}

    def insert_task(self, task_data: dict[str, Any]) -> None:
        """Insert a new task row."""
        values = tuple(task_data[column] for column in self._TASK_COLUMNS)

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(self._TASK_INSERT_SQL, values)
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                error_msg = str(exc).lower()
                if "unique" in error_msg:
                    raise DuplicateTaskError(
                        f"A task with task_id={task_data['task_id']} already exists"
                    ) from exc
                raise
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Fetch a task by ID."""
        with self._lock:
            cursor = self._db.execute(self._TASK_SELECT_BY_ID_SQL, (task_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> int:
        """Update task columns and return the number of affected rows."""
        if len(updates) == 0:
            return 0

        if any(column not in self._TASK_COLUMNS for column in updates):
            msg = "Attempted to update unknown task column"
            raise ValueError(msg)

        set_clause = ", ".join(f"{column} = ?" for column in updates)
        params: list[object] = list(updates.values())

        query = "UPDATE tasks SET " + set_clause + " WHERE task_id = ?"  # nosec B608
        params.append(task_id)
        if expected_status is not None:
            query += " AND status = ?"
            params.append(expected_status)

        with self._lock:
            cursor = self._db.execute(query, params)
            self._db.commit()
        return int(cursor.rowcount)

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        """List tasks with optional filters."""
        query = self._TASK_SELECT_BASE_SQL
        clauses: list[str] = []
        params: list[object] = []

        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if poster_id is not None:
            clauses.append("poster_id = ?")
            params.append(poster_id)
        if worker_id is not None:
            clauses.append("worker_id = ?")
            params.append(worker_id)

        if len(clauses) > 0:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY created_at DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        if offset is not None:
            query += " OFFSET ?"
            params.append(offset)

        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def count_tasks(self) -> int:
        """Count total tasks."""
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return int(row[0]) if row is not None else 0

    def count_tasks_by_status(self) -> dict[str, int]:
        """Count tasks grouped by status."""
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    def insert_bid(self, bid_data: dict[str, Any]) -> None:
        """Insert a bid and increment the associated task bid_count atomically."""
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    """
                    INSERT INTO bids (bid_id, task_id, bidder_id, amount, submitted_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        bid_data["bid_id"],
                        bid_data["task_id"],
                        bid_data["bidder_id"],
                        bid_data["amount"],
                        bid_data["submitted_at"],
                    ),
                )
                self._db.execute(
                    "UPDATE tasks SET bid_count = bid_count + 1 WHERE task_id = ?",
                    (bid_data["task_id"],),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                error_msg = str(exc).lower()
                if "unique" in error_msg:
                    raise DuplicateBidError("This agent already bid on this task") from exc
                raise
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
        """Fetch a bid by bid_id and task_id."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT bid_id, task_id, bidder_id, amount, submitted_at FROM bids "
                "WHERE bid_id = ? AND task_id = ?",
                (bid_id, task_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "bid_id": row["bid_id"],
            "task_id": row["task_id"],
            "bidder_id": row["bidder_id"],
            "amount": row["amount"],
            "submitted_at": row["submitted_at"],
        }

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Fetch all bids for a task sorted by submission time."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT bid_id, task_id, bidder_id, amount, submitted_at "
                "FROM bids WHERE task_id = ? ORDER BY submitted_at",
                (task_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "bid_id": row["bid_id"],
                "task_id": row["task_id"],
                "bidder_id": row["bidder_id"],
                "amount": row["amount"],
                "submitted_at": row["submitted_at"],
            }
            for row in rows
        ]

    def insert_asset(self, asset_data: dict[str, Any]) -> None:
        """Insert an asset record."""
        with self._lock:
            self._db.execute(
                """
                INSERT INTO assets (
                    asset_id, task_id, uploader_id, filename,
                    content_type, size_bytes, content_hash, uploaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_data["asset_id"],
                    asset_data["task_id"],
                    asset_data["uploader_id"],
                    asset_data["filename"],
                    asset_data["content_type"],
                    asset_data["size_bytes"],
                    asset_data["content_hash"],
                    asset_data["uploaded_at"],
                ),
            )
            self._db.commit()

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        """Fetch a single asset by ID for a task."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT asset_id, task_id, uploader_id, filename, content_type, size_bytes, "
                "content_hash, uploaded_at FROM assets WHERE asset_id = ? AND task_id = ?",
                (asset_id, task_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "asset_id": row["asset_id"],
            "task_id": row["task_id"],
            "uploader_id": row["uploader_id"],
            "filename": row["filename"],
            "content_type": row["content_type"],
            "size_bytes": row["size_bytes"],
            "content_hash": row["content_hash"],
            "uploaded_at": row["uploaded_at"],
        }

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Fetch all assets for a task sorted by upload time."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT asset_id, task_id, uploader_id, filename, content_type, size_bytes, "
                "content_hash, uploaded_at FROM assets WHERE task_id = ? ORDER BY uploaded_at",
                (task_id,),
            )
            rows = cursor.fetchall()
        return [
            {
                "asset_id": row["asset_id"],
                "task_id": row["task_id"],
                "uploader_id": row["uploader_id"],
                "filename": row["filename"],
                "content_type": row["content_type"],
                "size_bytes": row["size_bytes"],
                "content_hash": row["content_hash"],
                "uploaded_at": row["uploaded_at"],
            }
            for row in rows
        ]

    def count_assets(self, task_id: str) -> int:
        """Count assets for a task."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM assets WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()
