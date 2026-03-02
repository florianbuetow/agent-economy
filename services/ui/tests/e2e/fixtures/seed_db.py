"""Extended seed data for E2E tests — richer than integration seed data.

This module extends the integration seed data with additional agents, tasks,
events, and records to provide comprehensive coverage for browser-based E2E
tests. The integration seed data (5 agents, 12 tasks, 25 events) remains
the base — this module adds on top of it.

Usage:
    from tests.e2e.fixtures.seed_db import extend_seed_data
    extend_seed_data(conn)  # call AFTER insert_seed_data(conn)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

EXTRA_AGENTS = [
    (
        "a-frank",
        "Frank",
        "ed25519:RkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkZGRkY=",
        "2026-02-05T10:00:00Z",
    ),
    (
        "a-grace",
        "Grace",
        "ed25519:R0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0dHR0c=",
        "2026-02-10T11:00:00Z",
    ),
    (
        "a-heidi",
        "Heidi",
        "ed25519:SEhISEhISEhISEhISEhISEhISEhISEhISEhISEhISEg=",
        "2026-02-12T09:00:00Z",
    ),
    (
        "a-ivan",
        "Ivan",
        "ed25519:SUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUlJSUk=",
        "2026-02-15T14:00:00Z",
    ),
    (
        "a-judy",
        "Judy",
        "ed25519:SkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSkpKSg=",
        "2026-02-20T08:00:00Z",
    ),
]

EXTRA_BANK_ACCOUNTS = [
    ("a-frank", 600, "2026-02-05T10:00:00Z"),
    ("a-grace", 1500, "2026-02-10T11:00:00Z"),
    ("a-heidi", 200, "2026-02-12T09:00:00Z"),
    ("a-ivan", 900, "2026-02-15T14:00:00Z"),
    ("a-judy", 750, "2026-02-20T08:00:00Z"),
]

EXTRA_BANK_TRANSACTIONS = [
    (
        "tx-e1",
        "a-frank",
        "credit",
        1000,
        1000,
        "salary_r1_frank",
        "2026-02-05T12:00:00Z",
    ),
    (
        "tx-e2",
        "a-grace",
        "credit",
        1500,
        1500,
        "salary_r1_grace",
        "2026-02-10T12:00:00Z",
    ),
    (
        "tx-e3",
        "a-heidi",
        "credit",
        500,
        500,
        "salary_r1_heidi",
        "2026-02-12T12:00:00Z",
    ),
    (
        "tx-e4",
        "a-ivan",
        "credit",
        1000,
        1000,
        "salary_r1_ivan",
        "2026-02-15T16:00:00Z",
    ),
    (
        "tx-e5",
        "a-judy",
        "credit",
        1000,
        1000,
        "salary_r1_judy",
        "2026-02-20T10:00:00Z",
    ),
    (
        "tx-e6",
        "a-frank",
        "escrow_lock",
        120,
        880,
        "esc-e1",
        "2026-02-20T09:00:00Z",
    ),
    (
        "tx-e7",
        "a-grace",
        "escrow_lock",
        200,
        1300,
        "esc-e2",
        "2026-02-22T09:00:00Z",
    ),
    (
        "tx-e8",
        "a-ivan",
        "escrow_lock",
        80,
        920,
        "esc-e3",
        "2026-02-25T09:00:00Z",
    ),
]

EXTRA_BANK_ESCROW = [
    (
        "esc-e1",
        "a-frank",
        120,
        "t-task-e1",
        "released",
        "2026-02-20T09:00:00Z",
        "2026-03-01T10:00:00Z",
    ),
    (
        "esc-e2",
        "a-grace",
        200,
        "t-task-e2",
        "locked",
        "2026-02-22T09:00:00Z",
        None,
    ),
    (
        "esc-e3",
        "a-ivan",
        80,
        "t-task-e3",
        "locked",
        "2026-02-25T09:00:00Z",
        None,
    ),
]

EXTRA_BOARD_TASKS = [
    (
        "t-task-e1",
        "a-frank",
        "Payment Gateway",
        "Payment gateway integration spec",
        120,
        "approved",
        86400,
        604800,
        172800,
        "2026-02-21T09:00:00Z",
        "2026-02-28T10:00:00Z",
        "2026-03-01T10:00:00Z",
        "esc-e1",
        "a-grace",
        "bid-e1",
        None,
        None,
        None,
        None,
        "2026-02-20T09:00:00Z",
        "2026-02-21T10:00:00Z",
        "2026-02-26T16:00:00Z",
        "2026-03-01T10:00:00Z",
        None,
        None,
        None,
        None,
    ),
    (
        "t-task-e2",
        "a-grace",
        "Notification System",
        "Push notification spec",
        200,
        "submitted",
        86400,
        604800,
        172800,
        "2026-02-23T09:00:00Z",
        "2026-03-02T06:34:00Z",
        "2026-03-02T06:30:00Z",
        "esc-e2",
        "a-heidi",
        "bid-e3",
        None,
        None,
        None,
        None,
        "2026-02-22T09:00:00Z",
        "2026-02-23T12:00:00Z",
        "2026-03-02T06:36:00Z",
        None,
        None,
        None,
        None,
        None,
    ),
    (
        "t-task-e3",
        "a-ivan",
        "Analytics Dashboard",
        "Analytics integration spec",
        80,
        "open",
        86400,
        604800,
        172800,
        "2026-02-26T09:00:00Z",
        None,
        None,
        "esc-e3",
        None,
        None,
        None,
        None,
        None,
        None,
        "2026-02-25T09:00:00Z",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ),
]

EXTRA_BOARD_BIDS = [
    (
        "bid-e1",
        "t-task-e1",
        "a-grace",
        "Payment gateway expert",
        "2026-02-20T14:00:00Z",
    ),
    (
        "bid-e2",
        "t-task-e1",
        "a-heidi",
        "I can build payment systems",
        "2026-02-20T15:00:00Z",
    ),
    (
        "bid-e3",
        "t-task-e2",
        "a-heidi",
        "Notification system builder",
        "2026-02-22T14:00:00Z",
    ),
    (
        "bid-e4",
        "t-task-e2",
        "a-ivan",
        "Push notification specialist",
        "2026-02-22T16:00:00Z",
    ),
    (
        "bid-e5",
        "t-task-e3",
        "a-frank",
        "Analytics developer",
        "2026-02-25T14:00:00Z",
    ),
    (
        "bid-e6",
        "t-task-e3",
        "a-judy",
        "Dashboard specialist",
        "2026-02-25T15:00:00Z",
    ),
    (
        "bid-e7",
        "t-task-e3",
        "a-heidi",
        "Data visualization expert",
        "2026-02-25T16:00:00Z",
    ),
]

EXTRA_BOARD_ASSETS = [
    (
        "asset-e1",
        "t-task-e1",
        "a-grace",
        "payment-gateway.zip",
        "application/zip",
        307200,
        "/data/assets/asset-e1",
        "2026-02-26T15:00:00Z",
    ),
    (
        "asset-e2",
        "t-task-e2",
        "a-heidi",
        "notification-service.tar.gz",
        "application/gzip",
        409600,
        "/data/assets/asset-e2",
        "2026-03-02T06:35:00Z",
    ),
]

EXTRA_REPUTATION_FEEDBACK = [
    (
        "fb-e1",
        "t-task-e1",
        "a-frank",
        "a-grace",
        "poster",
        "delivery_quality",
        "extremely_satisfied",
        "Outstanding payment gateway",
        "2026-03-01T11:00:00Z",
        1,
    ),
    (
        "fb-e2",
        "t-task-e1",
        "a-grace",
        "a-frank",
        "worker",
        "spec_quality",
        "satisfied",
        "Good spec but could be more detailed",
        "2026-03-01T11:30:00Z",
        1,
    ),
]

EXTRA_EVENTS = [
    (
        26,
        "identity",
        "agent.registered",
        "2026-02-05T10:00:00Z",
        None,
        "a-frank",
        "Frank registered",
        {"agent_name": "Frank"},
    ),
    (
        27,
        "identity",
        "agent.registered",
        "2026-02-10T11:00:00Z",
        None,
        "a-grace",
        "Grace registered",
        {"agent_name": "Grace"},
    ),
    (
        28,
        "identity",
        "agent.registered",
        "2026-02-12T09:00:00Z",
        None,
        "a-heidi",
        "Heidi registered",
        {"agent_name": "Heidi"},
    ),
    (
        29,
        "identity",
        "agent.registered",
        "2026-02-15T14:00:00Z",
        None,
        "a-ivan",
        "Ivan registered",
        {"agent_name": "Ivan"},
    ),
    (
        30,
        "identity",
        "agent.registered",
        "2026-02-20T08:00:00Z",
        None,
        "a-judy",
        "Judy registered",
        {"agent_name": "Judy"},
    ),
    (
        31,
        "bank",
        "salary.paid",
        "2026-02-05T12:00:00Z",
        None,
        "a-frank",
        "Frank received salary",
        {"amount": 1000},
    ),
    (
        32,
        "bank",
        "salary.paid",
        "2026-02-10T12:00:00Z",
        None,
        "a-grace",
        "Grace received salary",
        {"amount": 1500},
    ),
    (
        33,
        "board",
        "task.created",
        "2026-02-20T09:00:00Z",
        "t-task-e1",
        "a-frank",
        "Frank posted Payment Gateway",
        {"title": "Payment Gateway", "reward": 120},
    ),
    (
        34,
        "board",
        "bid.submitted",
        "2026-02-20T14:00:00Z",
        "t-task-e1",
        "a-grace",
        "Grace bid on Payment Gateway",
        {"bid_id": "bid-e1"},
    ),
    (
        35,
        "board",
        "bid.submitted",
        "2026-02-20T15:00:00Z",
        "t-task-e1",
        "a-heidi",
        "Heidi bid on Payment Gateway",
        {"bid_id": "bid-e2"},
    ),
    (
        36,
        "board",
        "task.accepted",
        "2026-02-21T10:00:00Z",
        "t-task-e1",
        "a-frank",
        "Frank accepted Grace for Payment Gateway",
        {"worker_id": "a-grace", "worker_name": "Grace"},
    ),
    (
        37,
        "bank",
        "escrow.locked",
        "2026-02-20T09:00:00Z",
        "t-task-e1",
        "a-frank",
        "Escrow locked 120 for Payment Gateway",
        {"escrow_id": "esc-e1", "amount": 120},
    ),
    (
        38,
        "board",
        "task.submitted",
        "2026-02-26T16:00:00Z",
        "t-task-e1",
        "a-grace",
        "Grace submitted Payment Gateway",
        {"worker_name": "Grace", "asset_count": 1},
    ),
    (
        39,
        "board",
        "task.approved",
        "2026-03-01T10:00:00Z",
        "t-task-e1",
        "a-frank",
        "Frank approved Payment Gateway",
        {"reward": 120},
    ),
    (
        40,
        "bank",
        "escrow.released",
        "2026-03-01T10:00:00Z",
        "t-task-e1",
        "a-frank",
        "Escrow released 120 for Payment Gateway",
        {"escrow_id": "esc-e1", "amount": 120},
    ),
    (
        41,
        "reputation",
        "feedback.revealed",
        "2026-03-01T11:30:00Z",
        "t-task-e1",
        "a-frank",
        "Feedback revealed for Payment Gateway",
        {"category": "delivery_quality"},
    ),
    (
        42,
        "board",
        "task.created",
        "2026-02-22T09:00:00Z",
        "t-task-e2",
        "a-grace",
        "Grace posted Notification System",
        {"title": "Notification System", "reward": 200},
    ),
    (
        43,
        "board",
        "bid.submitted",
        "2026-02-22T14:00:00Z",
        "t-task-e2",
        "a-heidi",
        "Heidi bid on Notification System",
        {"bid_id": "bid-e3"},
    ),
    (
        44,
        "board",
        "bid.submitted",
        "2026-02-22T16:00:00Z",
        "t-task-e2",
        "a-ivan",
        "Ivan bid on Notification System",
        {"bid_id": "bid-e4"},
    ),
    (
        45,
        "board",
        "task.accepted",
        "2026-02-23T12:00:00Z",
        "t-task-e2",
        "a-grace",
        "Grace accepted Heidi for Notification System",
        {"worker_id": "a-heidi", "worker_name": "Heidi"},
    ),
    (
        46,
        "bank",
        "escrow.locked",
        "2026-02-22T09:00:00Z",
        "t-task-e2",
        "a-grace",
        "Escrow locked 200 for Notification System",
        {"escrow_id": "esc-e2", "amount": 200},
    ),
    (
        47,
        "board",
        "task.submitted",
        "2026-03-02T06:36:00Z",
        "t-task-e2",
        "a-heidi",
        "Heidi submitted Notification System",
        {"worker_name": "Heidi", "asset_count": 1},
    ),
    (
        48,
        "board",
        "task.created",
        "2026-02-25T09:00:00Z",
        "t-task-e3",
        "a-ivan",
        "Ivan posted Analytics Dashboard",
        {"title": "Analytics Dashboard", "reward": 80},
    ),
    (
        49,
        "board",
        "bid.submitted",
        "2026-02-25T14:00:00Z",
        "t-task-e3",
        "a-frank",
        "Frank bid on Analytics Dashboard",
        {"bid_id": "bid-e5"},
    ),
    (
        50,
        "board",
        "bid.submitted",
        "2026-02-25T15:00:00Z",
        "t-task-e3",
        "a-judy",
        "Judy bid on Analytics Dashboard",
        {"bid_id": "bid-e6"},
    ),
    (
        51,
        "board",
        "bid.submitted",
        "2026-02-25T16:00:00Z",
        "t-task-e3",
        "a-heidi",
        "Heidi bid on Analytics Dashboard",
        {"bid_id": "bid-e7"},
    ),
    (
        52,
        "bank",
        "escrow.locked",
        "2026-02-25T09:00:00Z",
        "t-task-e3",
        "a-ivan",
        "Escrow locked 80 for Analytics Dashboard",
        {"escrow_id": "esc-e3", "amount": 80},
    ),
    (
        53,
        "bank",
        "salary.paid",
        "2026-02-12T12:00:00Z",
        None,
        "a-heidi",
        "Heidi received salary",
        {"amount": 500},
    ),
    (
        54,
        "bank",
        "salary.paid",
        "2026-02-15T16:00:00Z",
        None,
        "a-ivan",
        "Ivan received salary",
        {"amount": 1000},
    ),
    (
        55,
        "bank",
        "salary.paid",
        "2026-02-20T10:00:00Z",
        None,
        "a-judy",
        "Judy received salary",
        {"amount": 1000},
    ),
]


def extend_seed_data(conn: sqlite3.Connection) -> None:
    """Add extended E2E fixture data on top of integration seed data."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executemany(
            "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
            "VALUES (?, ?, ?, ?)",
            EXTRA_AGENTS,
        )
        conn.executemany(
            "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
            EXTRA_BANK_ACCOUNTS,
        )
        conn.executemany(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            EXTRA_BANK_TRANSACTIONS,
        )
        conn.executemany(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            EXTRA_BANK_ESCROW,
        )
        conn.executemany(
            "INSERT INTO board_tasks "
            "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
            "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
            "review_deadline, escrow_id, worker_id, accepted_bid_id, dispute_reason, ruling_id, "
            "worker_pct, ruling_summary, created_at, accepted_at, submitted_at, approved_at, "
            "cancelled_at, disputed_at, ruled_at, expired_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?)",
            EXTRA_BOARD_TASKS,
        )
        conn.executemany(
            "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, submitted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            EXTRA_BOARD_BIDS,
        )
        conn.executemany(
            "INSERT INTO board_assets "
            "(asset_id, task_id, uploader_id, filename, content_type, size_bytes, storage_path, "
            "uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            EXTRA_BOARD_ASSETS,
        )
        conn.executemany(
            "INSERT INTO reputation_feedback "
            "(feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating, comment, "
            "submitted_at, visible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            EXTRA_REPUTATION_FEEDBACK,
        )

        events_payload_rows = [
            (
                event_id,
                event_source,
                event_type,
                timestamp,
                task_id,
                agent_id,
                summary,
                json.dumps(payload),
            )
            for (
                event_id,
                event_source,
                event_type,
                timestamp,
                task_id,
                agent_id,
                summary,
                payload,
            ) in EXTRA_EVENTS
        ]
        conn.executemany(
            "INSERT INTO events "
            "(event_id, event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            events_payload_rows,
        )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
