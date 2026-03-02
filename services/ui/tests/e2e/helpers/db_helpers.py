"""Database mutation and SSE event injection helpers for E2E tests.

These helpers allow tests to mutate the database at runtime and verify
that SSE-driven UI updates reflect the changes in the browser.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Page


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_event_id(conn: sqlite3.Connection) -> int:
    """Return the next available event_id."""
    row = conn.execute("SELECT MAX(event_id) FROM events").fetchone()
    return (row[0] or 0) + 1


def insert_event(
    conn: sqlite3.Connection,
    event_source: str,
    event_type: str,
    summary: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    agent_id: str | None = None,
    timestamp: str | None = None,
) -> int:
    """Insert an event into the events table for SSE pickup."""
    event_id = _next_event_id(conn)
    ts = timestamp or _now_iso()
    conn.execute(
        "INSERT INTO events (event_id, event_source, event_type, timestamp, "
        "task_id, agent_id, summary, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            event_source,
            event_type,
            ts,
            task_id,
            agent_id,
            summary,
            json.dumps(payload),
        ),
    )
    conn.commit()
    return event_id


def advance_task_status(
    conn: sqlite3.Connection,
    task_id: str,
    new_status: str,
) -> None:
    """Update a task's status in board_tasks."""
    conn.execute(
        "UPDATE board_tasks SET status = ? WHERE task_id = ?",
        (new_status, task_id),
    )
    conn.commit()


def add_bid(
    conn: sqlite3.Connection,
    bid_id: str,
    task_id: str,
    bidder_id: str,
    proposal: str,
    submitted_at: str | None = None,
) -> None:
    """Insert a bid into board_bids."""
    ts = submitted_at or _now_iso()
    conn.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (bid_id, task_id, bidder_id, proposal, ts),
    )
    conn.commit()


def add_feedback(
    conn: sqlite3.Connection,
    feedback_id: str,
    task_id: str,
    from_agent_id: str,
    to_agent_id: str,
    role: str,
    category: str,
    rating: str,
    comment: str | None = None,
    submitted_at: str | None = None,
    visible: int = 0,
) -> None:
    """Insert feedback into reputation_feedback."""
    ts = submitted_at or _now_iso()
    conn.execute(
        "INSERT INTO reputation_feedback "
        "(feedback_id, task_id, from_agent_id, to_agent_id, role, category, "
        "rating, comment, submitted_at, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            feedback_id,
            task_id,
            from_agent_id,
            to_agent_id,
            role,
            category,
            rating,
            comment,
            ts,
            visible,
        ),
    )
    conn.commit()


def create_escrow(
    conn: sqlite3.Connection,
    escrow_id: str,
    payer_account_id: str,
    amount: int,
    task_id: str,
    status: str = "locked",
    created_at: str | None = None,
    resolved_at: str | None = None,
) -> None:
    """Insert an escrow record into bank_escrow."""
    ts = created_at or _now_iso()
    conn.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (escrow_id, payer_account_id, amount, task_id, status, ts, resolved_at),
    )
    conn.commit()


def add_court_claim(
    conn: sqlite3.Connection,
    claim_id: str,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    reason: str,
    status: str = "filed",
    filed_at: str | None = None,
) -> None:
    """Insert a court claim into court_claims."""
    ts = filed_at or _now_iso()
    conn.execute(
        "INSERT INTO court_claims "
        "(claim_id, task_id, claimant_id, respondent_id, reason, status, filed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (claim_id, task_id, claimant_id, respondent_id, reason, status, ts),
    )
    conn.commit()


def wait_for_sse_update(page: Page, timeout: float = 5000) -> None:
    """Wait for the DOM to update after an SSE event injection."""
    current_count = page.locator(".feed-item").count()
    with suppress(Exception):
        page.wait_for_function(
            f"document.querySelectorAll('.feed-item').length > {current_count}",
            timeout=timeout,
        )


def get_writable_db_connection(db_path: str) -> sqlite3.Connection:
    """Open a writable connection to the E2E test database."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn
