"""SQLite-backed agent storage."""

from __future__ import annotations

import contextlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock


class DuplicateAgentError(Exception):
    """Raised when a duplicate public key is inserted."""


class AgentStore:
    """SQLite-backed agent storage with thread-safe transactions."""

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
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    public_key TEXT NOT NULL UNIQUE,
                    registered_at TEXT NOT NULL
                );
                """
            )
            self._db.commit()

    def insert(self, name: str, public_key: str) -> dict[str, str]:
        """Insert a new agent. Returns the agent record dict."""
        agent_id = f"a-{uuid.uuid4()}"
        registered_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                self._db.execute(
                    "INSERT INTO agents (agent_id, name, public_key, registered_at) "
                    "VALUES (?, ?, ?, ?)",
                    (agent_id, name, public_key, registered_at),
                )
                self._db.commit()
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                error_msg = str(exc).lower()
                if "unique" in error_msg:
                    raise DuplicateAgentError(
                        f"Public key already registered: {public_key}"
                    ) from exc
                raise
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise

        return {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
        }

    def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """Look up a single agent by ID. Returns None if not found."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT agent_id, name, public_key, registered_at FROM agents WHERE agent_id = ?",
                (agent_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": str(row["agent_id"]),
            "name": str(row["name"]),
            "public_key": str(row["public_key"]),
            "registered_at": str(row["registered_at"]),
        }

    def list_all(self) -> list[dict[str, str]]:
        """List all agents (without public keys). Sorted by registered_at."""
        with self._lock:
            cursor = self._db.execute(
                "SELECT agent_id, name, registered_at FROM agents ORDER BY registered_at"
            )
            rows = cursor.fetchall()
        return [
            {
                "agent_id": str(row["agent_id"]),
                "name": str(row["name"]),
                "registered_at": str(row["registered_at"]),
            }
            for row in rows
        ]

    def count(self) -> int:
        """Count total registered agents."""
        with self._lock:
            cursor = self._db.execute("SELECT COUNT(*) FROM agents")
            row = cursor.fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._db.close()
