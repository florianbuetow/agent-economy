"""Legacy Bank attestations retain absent native event/time metadata at cutover."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import migration
from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from tests.fakes import mp05_shared_legacy_history as history
from tests.fakes.mp05_publication_migration_history import publish_verified_backup

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _financial_rows(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        "accounts": connection.execute(
            "SELECT * FROM bank_accounts ORDER BY account_id"
        ).fetchall(),
        "transactions": connection.execute(
            "SELECT * FROM bank_transactions ORDER BY tx_id"
        ).fetchall(),
        "escrows": connection.execute("SELECT * FROM bank_escrow ORDER BY escrow_id").fetchall(),
        "events": connection.execute("SELECT * FROM events ORDER BY event_id").fetchall(),
        "receipts": connection.execute(
            "SELECT * FROM bank_operation_receipts ORDER BY operation_id"
        ).fetchall(),
    }


@pytest.mark.parametrize("missing", ["timestamp", "event", "both", "none"])
def test_attested_legacy_lock_preserves_missing_native_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    original = history._add_second_payer_lock

    def seed_sparse_legacy_lock(connection: sqlite3.Connection) -> None:
        original(connection)
        # Change only disposable V0 fixture input, before every verified cutover.
        if missing in {"timestamp", "both"}:
            connection.execute(
                "UPDATE bank_escrow SET created_at='' WHERE escrow_id='esc-bank-worker'"
            )
        if missing in {"event", "both"}:
            connection.execute(
                "UPDATE bank_escrow SET event_id=NULL WHERE escrow_id='esc-bank-worker'"
            )

    monkeypatch.setattr(history, "_add_second_payer_lock", seed_sparse_legacy_lock)
    source = history.prepare_v3_source_with_shared_legacy_locks(tmp_path)
    with sqlite3.connect(source.database) as connection:
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        before = _financial_rows(connection)
        original_metadata = connection.execute(
            "SELECT created_at,event_id FROM bank_escrow WHERE escrow_id='esc-bank-worker'"
        ).fetchone()
        assert original_metadata is not None
    backup_id = f"sparse-{missing}"
    publish_verified_backup(source, backup_id=backup_id)

    result = migration.migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )

    assert result.applied
    assert result.observation == "committed_v4_verified"
    with sqlite3.connect(source.database) as connection:
        assert _financial_rows(connection) == before
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_publications(connection).publications_clean
        row = connection.execute(
            "SELECT observed_fact_json,state,refund_receipt_event_id "
            "FROM board_legacy_posting_observations WHERE escrow_id='esc-bank-worker'"
        ).fetchone()
        assert row is not None
        fact = json.loads(row[0])
        assert fact["lock_provenance"]["kind"] == "legacy"
        assert (fact["escrow"]["created_at"], fact["escrow"]["event_id"]) == original_metadata
        assert row[1] == "refund_pending"
        assert row[2] is None
