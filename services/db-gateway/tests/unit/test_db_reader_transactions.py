"""T-105: DbReader.get_transactions must SELECT event_id — the column has
existed on bank_transactions since H-5 (idempotent-replay event ids), but the
reader's query never picked it up, so every transaction row silently dropped
its event_id when read back through GET /bank/accounts/{id}/transactions.
"""

from __future__ import annotations

import sqlite3

import pytest

from db_gateway_service.services.db_reader import DbReader


def _insert_account_with_transaction(
    conn: sqlite3.Connection,
    account_id: str,
    tx_id: str,
    event_id: int | None,
) -> None:
    """Insert an agent, account, and one bank_transactions row directly into SQLite."""
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        (account_id, "Test Agent", "ed25519:key-1", "2026-03-01T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, ?)",
        (account_id, 500, "2026-03-01T10:00:00Z"),
    )
    conn.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES (?, ?, 'credit', 500, 500, 'initial_balance', ?, ?)",
        (tx_id, account_id, "2026-03-01T10:00:00Z", event_id),
    )
    conn.commit()


@pytest.mark.unit
class TestDbReaderBankTransactions:
    """Direct DbReader tests for get_transactions' event_id column (T-105)."""

    def test_get_transactions_includes_event_id(self, initialized_db: str) -> None:
        """get_transactions returns the row's event_id, not just the legacy columns."""
        conn = sqlite3.connect(initialized_db)
        _insert_account_with_transaction(conn, "a-1", "tx-1", event_id=42)
        reader = DbReader(db=conn)

        result = reader.get_transactions("a-1")

        assert len(result) == 1
        assert result[0]["tx_id"] == "tx-1"
        assert result[0]["event_id"] == 42
        conn.close()

    def test_get_transactions_event_id_null_for_legacy_rows(self, initialized_db: str) -> None:
        """A row written before the event_id column existed reads back as None, not omitted."""
        conn = sqlite3.connect(initialized_db)
        _insert_account_with_transaction(conn, "a-1", "tx-1", event_id=None)
        reader = DbReader(db=conn)

        result = reader.get_transactions("a-1")

        assert len(result) == 1
        assert "event_id" in result[0]
        assert result[0]["event_id"] is None
        conn.close()
