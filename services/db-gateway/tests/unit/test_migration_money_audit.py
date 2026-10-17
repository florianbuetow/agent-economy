"""Failing-first coverage for the read-only MP-06 migration money audit."""

from __future__ import annotations

import hashlib
import importlib
import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service.schema import SQLITE_MAX_INTEGER, SchemaState, load_schema_sql

if TYPE_CHECKING:
    from pathlib import Path

    from db_gateway_service.money_audit import MoneyAuditResult

pytestmark = pytest.mark.unit


def _audit(connection: sqlite3.Connection) -> MoneyAuditResult:
    from db_gateway_service.money_audit import audit_money

    return audit_money(connection)


def _legacy_connection(path: str = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(load_schema_sql(SchemaState.SUPPORTED_V0))
    return connection


def _add_account(connection: sqlite3.Connection, account_id: str, balance: object) -> None:
    connection.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, 'now')",
        (account_id, account_id, f"key-{account_id}"),
    )
    connection.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, ?, 'now')",
        (account_id, balance),
    )


def _add_transaction(
    connection: sqlite3.Connection,
    *,
    tx_id: str,
    account_id: str,
    transaction_type: object,
    amount: object,
    balance_after: object,
    reference: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, 'now')",
        (
            tx_id,
            account_id,
            transaction_type,
            amount,
            balance_after,
            reference if reference is not None else f"ref-{tx_id}",
        ),
    )


def _add_escrow(
    connection: sqlite3.Connection,
    *,
    escrow_id: str,
    payer_id: str,
    amount: int,
    task_id: str,
    status: str,
) -> None:
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'now')",
        (escrow_id, payer_id, amount, task_id, status),
    )


def _add_task(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    poster_id: str,
    reward: int,
    escrow_id: str,
) -> None:
    connection.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
        "VALUES (?, ?, 'title', 'spec', ?, 'open', 1, 1, 1, 'now', ?, 'now')",
        (task_id, poster_id, reward, escrow_id),
    )


def _finding_keys(result: MoneyAuditResult) -> set[tuple[str, str, str, tuple[str, ...]]]:
    return {
        (finding.code, finding.table, finding.column, finding.record_ids)
        for finding in result.findings
    }


def test_money_audit_api_exists_without_collection_failure() -> None:
    """New-feature red proof: absence fails in the test body, not collection."""
    try:
        module = importlib.import_module("db_gateway_service.money_audit")
    except ModuleNotFoundError:
        pytest.fail("money audit API is not implemented")
    assert callable(module.audit_money)


def test_zero_account_legacy_database_is_clean() -> None:
    connection = _legacy_connection()
    try:
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True
    assert result.financially_eligible is True
    assert result.findings == ()
    assert result.total_credit_issuance == 0
    assert result.recorded_burn == 0
    assert result.total_account_balances == 0
    assert result.total_locked_escrow == 0
    assert result.account_reconciliations == ()


def test_python_integer_aggregation_exceeds_sqlite_integer_without_overflow() -> None:
    connection = _legacy_connection()
    try:
        for suffix in ("one", "two"):
            account_id = f"a-{suffix}"
            _add_account(connection, account_id, SQLITE_MAX_INTEGER)
            _add_transaction(
                connection,
                tx_id=f"tx-{suffix}",
                account_id=account_id,
                transaction_type="credit",
                amount=SQLITE_MAX_INTEGER,
                balance_after=SQLITE_MAX_INTEGER,
            )
        result = _audit(connection)
    finally:
        connection.close()

    expected = SQLITE_MAX_INTEGER * 2
    assert result.financially_clean is True
    assert result.total_credit_issuance == expected
    assert result.total_account_balances == expected
    assert result.total_locked_escrow == 0


def test_normal_lock_and_split_release_reconciles() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 60)
        _add_account(connection, "a-worker", 40)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=60,
            balance_after=40,
            reference="t-1",
        )
        _add_transaction(
            connection,
            tx_id="tx-worker",
            account_id="a-worker",
            transaction_type="escrow_release",
            amount=40,
            balance_after=40,
            reference="esc-1",
        )
        _add_transaction(
            connection,
            tx_id="tx-poster",
            account_id="a-payer",
            transaction_type="escrow_release",
            amount=20,
            balance_after=60,
            reference="esc-1",
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
            "VALUES ('esc-1', 'a-payer', 60, 't-1', 'split', 'now')"
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True
    assert result.total_credit_issuance == 100
    assert result.total_account_balances == 100
    assert result.total_locked_escrow == 0
    assert all(item.matches for item in result.account_reconciliations or ())


@pytest.mark.parametrize(
    ("escrow_status", "payer_balance", "expected_locked"),
    [("locked", 40, 60), ("released", 40, 0)],
)
def test_normal_locked_or_single_release_reconciles(
    escrow_status: str, payer_balance: int, expected_locked: int
) -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", payer_balance)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=60,
            balance_after=40,
            reference="t-1",
        )
        if escrow_status == "released":
            _add_account(connection, "a-recipient", 60)
            _add_transaction(
                connection,
                tx_id="tx-release",
                account_id="a-recipient",
                transaction_type="escrow_release",
                amount=60,
                balance_after=60,
                reference="esc-1",
            )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
            "VALUES ('esc-1', 'a-payer', 60, 't-1', ?, 'now')",
            (escrow_status,),
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True
    assert result.total_credit_issuance == 100
    assert result.total_account_balances == 100 - expected_locked
    assert result.total_locked_escrow == expected_locked


def test_supply_mismatch_is_reported() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-1", 9)
        _add_transaction(
            connection,
            tx_id="tx-1",
            account_id="a-1",
            transaction_type="credit",
            amount=10,
            balance_after=10,
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is False
    assert result.total_credit_issuance == 10
    assert result.total_account_balances == 9
    assert (
        "supply_mismatch",
        "bank_transactions",
        "amount",
        (),
    ) in _finding_keys(result)


def test_per_account_drift_is_found_when_global_supply_still_balances() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-1", 40)
        _add_account(connection, "a-2", 60)
        _add_transaction(
            connection,
            tx_id="tx-1",
            account_id="a-1",
            transaction_type="credit",
            amount=50,
            balance_after=50,
        )
        _add_transaction(
            connection,
            tx_id="tx-2",
            account_id="a-2",
            transaction_type="credit",
            amount=50,
            balance_after=50,
        )
        result = _audit(connection)
    finally:
        connection.close()

    keys = _finding_keys(result)
    assert ("account_ledger_drift", "bank_accounts", "balance", ("a-1",)) in keys
    assert ("account_ledger_drift", "bank_accounts", "balance", ("a-2",)) in keys
    assert not any(finding.code == "supply_mismatch" for finding in result.findings)


def test_invalid_storage_signs_bounds_and_vocab_make_totals_unavailable() -> None:
    connection = _legacy_connection()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        _add_account(connection, "a-bad", "not-an-integer")
        _add_account(connection, "a-negative", -1)
        _add_account(connection, "a-huge", float(SQLITE_MAX_INTEGER) * 2)
        _add_transaction(
            connection,
            tx_id="tx-zero",
            account_id="a-bad",
            transaction_type="credit",
            amount=0,
            balance_after="bad",
        )
        _add_transaction(
            connection,
            tx_id="tx-type",
            account_id="a-bad",
            transaction_type="mint",
            amount=1,
            balance_after=1,
        )
        _add_transaction(
            connection,
            tx_id="tx-negative",
            account_id="a-negative",
            transaction_type="credit",
            amount=-1,
            balance_after=-1,
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
            "VALUES ('esc-bad', 'a-bad', 0, 't-bad', 'pending', 'now')"
        )
        connection.execute(
            "INSERT INTO board_tasks "
            "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
            "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
            "VALUES ('t-bad', 'a-bad', 'bad', 'bad', 0, 'open', 1, 1, 1, 'now', "
            "'esc-bad', 'now')"
        )
        connection.execute(
            "INSERT INTO board_bids "
            "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
            "VALUES ('bid-bad', 't-bad', 'a-negative', 'bad', -1, 'now')"
        )
        result = _audit(connection)
    finally:
        connection.close()

    codes = {finding.code for finding in result.findings}
    assert {
        "invalid_money_storage",
        "invalid_money_sign",
        "money_out_of_range",
        "unsupported_transaction_type",
        "unknown_escrow_status",
    }.issubset(codes)
    assert result.total_credit_issuance is None
    assert result.total_account_balances is None
    assert result.total_locked_escrow is None
    assert result.account_reconciliations is None


def test_orphan_account_references_and_lifetime_duplicate_escrows_are_reported() -> None:
    connection = _legacy_connection()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        _add_account(connection, "a-real", 0)
        _add_transaction(
            connection,
            tx_id="tx-orphan",
            account_id="a-missing",
            transaction_type="credit",
            amount=1,
            balance_after=1,
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at) VALUES "
            "('esc-released', 'a-missing', 1, 't-1', 'released', 'now'), "
            "('esc-locked', 'a-missing', 1, 't-1', 'locked', 'now')"
        )
        result = _audit(connection)
    finally:
        connection.close()

    keys = _finding_keys(result)
    assert (
        "orphan_transaction_account",
        "bank_transactions",
        "account_id",
        ("tx-orphan", "a-missing"),
    ) in keys
    assert (
        "orphan_escrow_payer_account",
        "bank_escrow",
        "payer_account_id",
        ("esc-locked", "a-missing"),
    ) in keys
    duplicate = next(
        finding for finding in result.findings if finding.code == "duplicate_lifetime_escrow"
    )
    assert duplicate.record_ids == ("a-missing", "t-1", "esc-locked", "esc-released")


def test_missing_money_column_is_explicit_and_totals_are_unavailable() -> None:
    connection = _legacy_connection()
    try:
        connection.execute("ALTER TABLE board_bids DROP COLUMN amount")
        result = _audit(connection)
    finally:
        connection.close()

    assert (
        "missing_money_column",
        "board_bids",
        "amount",
        (),
    ) in _finding_keys(result)
    assert result.total_credit_issuance is None
    assert result.total_account_balances is None
    assert result.total_locked_escrow is None
    assert result.account_reconciliations is None


def test_audit_does_not_mutate_connection_rows_or_database_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    backup_artifact = tmp_path / "immutable-backup.marker"
    backup_artifact.write_bytes(b"verified-backup-evidence")
    connection = _legacy_connection(str(path))
    try:
        _add_account(connection, "a-1", 10)
        _add_transaction(
            connection,
            tx_id="tx-1",
            account_id="a-1",
            transaction_type="credit",
            amount=10,
            balance_after=10,
        )
        connection.commit()
        before_dump = tuple(connection.iterdump())
        before_changes = connection.total_changes
        before_transaction = connection.in_transaction
        before_bytes = path.read_bytes()
        before_digest = hashlib.sha256(before_bytes).hexdigest()
        before_backup = backup_artifact.read_bytes()

        result = _audit(connection)

        assert result.financially_clean is True
        assert tuple(connection.iterdump()) == before_dump
        assert connection.total_changes == before_changes
        assert connection.in_transaction is before_transaction
        assert path.read_bytes() == before_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before_digest
        assert backup_artifact.read_bytes() == before_backup
    finally:
        connection.close()


def test_balanced_lock_and_release_legs_without_escrows_are_rejected() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 40)
        _add_account(connection, "a-recipient", 60)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=60,
            balance_after=40,
            reference="t-missing",
        )
        _add_transaction(
            connection,
            tx_id="tx-release",
            account_id="a-recipient",
            transaction_type="escrow_release",
            amount=60,
            balance_after=60,
            reference="esc-missing",
        )
        result = _audit(connection)
    finally:
        connection.close()

    codes = {finding.code for finding in result.findings}
    assert {"lock_without_escrow", "release_without_escrow"}.issubset(codes)
    assert "supply_mismatch" not in codes
    assert "account_ledger_drift" not in codes


@pytest.mark.parametrize("damage", ["payer", "reference", "amount"])
def test_exact_escrow_lock_leg_must_match_payer_task_and_amount(damage: str) -> None:
    connection = _legacy_connection()
    try:
        _add_account(
            connection,
            "a-payer",
            0 if damage == "amount" else 100 if damage == "payer" else 40,
        )
        if damage == "payer":
            _add_account(connection, "a-other", 0)
            _add_transaction(
                connection,
                tx_id="tx-other-credit",
                account_id="a-other",
                transaction_type="credit",
                amount=60,
                balance_after=60,
            )
        credit_amount = 110 if damage == "amount" else 100
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=credit_amount,
            balance_after=credit_amount,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-other" if damage == "payer" else "a-payer",
            transaction_type="escrow_lock",
            amount=50 if damage == "amount" else 60,
            balance_after=0 if damage in {"payer", "amount"} else 40,
            reference="t-wrong" if damage == "reference" else "t-1",
        )
        _add_escrow(
            connection,
            escrow_id="esc-1",
            payer_id="a-payer",
            amount=60,
            task_id="t-1",
            status="locked",
        )
        if damage == "amount":
            _add_transaction(
                connection,
                tx_id="tx-lock-compensating",
                account_id="a-payer",
                transaction_type="escrow_lock",
                amount=60,
                balance_after=0,
                reference="t-2",
            )
            _add_escrow(
                connection,
                escrow_id="esc-2",
                payer_id="a-payer",
                amount=50,
                task_id="t-2",
                status="locked",
            )
        result = _audit(connection)
    finally:
        connection.close()

    codes = {finding.code for finding in result.findings}
    assert f"escrow_lock_{damage}_mismatch" in codes
    assert "lock_without_escrow" in codes
    assert "supply_mismatch" not in codes
    assert "account_ledger_drift" not in codes


def test_locked_release_and_missing_resolved_release_can_balance_but_are_rejected() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 80)
        _add_account(connection, "a-recipient", 60)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=200,
            balance_after=200,
        )
        for suffix, task_id in (("locked", "t-locked"), ("released", "t-released")):
            _add_transaction(
                connection,
                tx_id=f"tx-lock-{suffix}",
                account_id="a-payer",
                transaction_type="escrow_lock",
                amount=60,
                balance_after=140 if suffix == "locked" else 80,
                reference=task_id,
            )
            _add_escrow(
                connection,
                escrow_id=f"esc-{suffix}",
                payer_id="a-payer",
                amount=60,
                task_id=task_id,
                status=suffix,
            )
        _add_transaction(
            connection,
            tx_id="tx-release-borrowed",
            account_id="a-recipient",
            transaction_type="escrow_release",
            amount=60,
            balance_after=60,
            reference="esc-locked",
        )
        result = _audit(connection)
    finally:
        connection.close()

    codes = {finding.code for finding in result.findings}
    assert "locked_escrow_has_release" in codes
    assert "resolved_escrow_release_count" in codes
    assert "supply_mismatch" not in codes
    assert "account_ledger_drift" not in codes


def test_two_resolved_escrows_with_under_and_over_release_totals_are_rejected() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 0)
        _add_account(connection, "a-recipient", 100)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        for suffix, amount in (("under", 60), ("over", 40)):
            _add_transaction(
                connection,
                tx_id=f"tx-lock-{suffix}",
                account_id="a-payer",
                transaction_type="escrow_lock",
                amount=amount,
                balance_after=40 if suffix == "under" else 0,
                reference=f"t-{suffix}",
            )
            _add_escrow(
                connection,
                escrow_id=f"esc-{suffix}",
                payer_id="a-payer",
                amount=amount,
                task_id=f"t-{suffix}",
                status="released",
            )
            _add_transaction(
                connection,
                tx_id=f"tx-release-{suffix}",
                account_id="a-recipient",
                transaction_type="escrow_release",
                amount=50,
                balance_after=50 if suffix == "under" else 100,
                reference=f"esc-{suffix}",
            )
        result = _audit(connection)
    finally:
        connection.close()

    amount_findings = [
        finding for finding in result.findings if finding.code == "resolved_escrow_release_amount"
    ]
    assert len(amount_findings) == 2
    assert not any(finding.code == "supply_mismatch" for finding in result.findings)
    assert not any(finding.code == "account_ledger_drift" for finding in result.findings)


@pytest.mark.parametrize("damage", ["missing", "payer", "task", "reward"])
def test_existing_board_task_must_match_its_escrow(damage: str) -> None:
    connection = _legacy_connection()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        _add_account(connection, "a-payer", 40)
        _add_account(connection, "a-other", 0)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=60,
            balance_after=40,
            reference="t-1" if damage != "task" else "t-other",
        )
        _add_escrow(
            connection,
            escrow_id="esc-1",
            payer_id="a-payer",
            amount=60,
            task_id="t-1" if damage != "task" else "t-other",
            status="locked",
        )
        _add_task(
            connection,
            task_id="t-1",
            poster_id="a-other" if damage == "payer" else "a-payer",
            reward=50 if damage == "reward" else 60,
            escrow_id="esc-missing" if damage == "missing" else "esc-1",
        )
        result = _audit(connection)
    finally:
        connection.close()

    expected_code = (
        "task_escrow_missing" if damage == "missing" else f"task_escrow_{damage}_mismatch"
    )
    assert expected_code in {finding.code for finding in result.findings}


def test_valid_existing_task_binding_and_bank_only_escrow_are_both_allowed() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 20)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        for suffix in ("task", "bank-only"):
            _add_transaction(
                connection,
                tx_id=f"tx-lock-{suffix}",
                account_id="a-payer",
                transaction_type="escrow_lock",
                amount=40,
                balance_after=60 if suffix == "task" else 20,
                reference=f"t-{suffix}",
            )
            _add_escrow(
                connection,
                escrow_id=f"esc-{suffix}",
                payer_id="a-payer",
                amount=40,
                task_id=f"t-{suffix}",
                status="locked",
            )
        _add_task(
            connection,
            task_id="t-task",
            poster_id="a-payer",
            reward=40,
            escrow_id="esc-task",
        )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True


@pytest.mark.parametrize(
    ("release_amounts", "expected_clean"),
    [((30, 30), True), ((20, 20, 20), False)],
    ids=["same-account-two-leg", "three-leg-invalid"],
)
def test_split_allows_same_account_but_only_one_or_two_positive_legs(
    release_amounts: tuple[int, ...], expected_clean: bool
) -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 100)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=60,
            balance_after=40,
            reference="t-1",
        )
        _add_escrow(
            connection,
            escrow_id="esc-1",
            payer_id="a-payer",
            amount=60,
            task_id="t-1",
            status="split",
        )
        running_balance = 40
        for index, amount in enumerate(release_amounts, start=1):
            running_balance += amount
            _add_transaction(
                connection,
                tx_id=f"tx-release-{index}",
                account_id="a-payer",
                transaction_type="escrow_release",
                amount=amount,
                balance_after=running_balance,
                reference="esc-1",
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is expected_clean
    count_codes = [
        finding.code
        for finding in result.findings
        if finding.code == "resolved_escrow_release_count"
    ]
    assert bool(count_codes) is (not expected_clean)


def test_two_leg_split_without_payer_leg_is_rejected_even_when_balanced() -> None:
    connection = _legacy_connection()
    try:
        _add_account(connection, "a-payer", 0)
        _add_account(connection, "a-worker-one", 40)
        _add_account(connection, "a-worker-two", 60)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=100,
            balance_after=0,
            reference="t-1",
        )
        _add_escrow(
            connection,
            escrow_id="esc-1",
            payer_id="a-payer",
            amount=100,
            task_id="t-1",
            status="split",
        )
        for suffix, account_id, amount in (
            ("one", "a-worker-one", 40),
            ("two", "a-worker-two", 60),
        ):
            _add_transaction(
                connection,
                tx_id=f"tx-release-{suffix}",
                account_id=account_id,
                transaction_type="escrow_release",
                amount=amount,
                balance_after=amount,
                reference="esc-1",
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert "split_payer_release_missing" in {finding.code for finding in result.findings}
    assert not any(finding.code == "supply_mismatch" for finding in result.findings)
    assert not any(finding.code == "account_ledger_drift" for finding in result.findings)


@pytest.mark.parametrize(
    "release_accounts",
    [("a-worker",), ("a-payer",), ("a-payer", "a-payer")],
    ids=["full-worker", "full-payer", "two-payer-legs"],
)
def test_split_payer_leg_rule_preserves_legitimate_shapes(
    release_accounts: tuple[str, ...],
) -> None:
    connection = _legacy_connection()
    try:
        recipient_ids = set(release_accounts)
        _add_account(connection, "a-payer", 100 if recipient_ids == {"a-payer"} else 0)
        if "a-worker" in recipient_ids:
            _add_account(connection, "a-worker", 100)
        _add_transaction(
            connection,
            tx_id="tx-credit",
            account_id="a-payer",
            transaction_type="credit",
            amount=100,
            balance_after=100,
        )
        _add_transaction(
            connection,
            tx_id="tx-lock",
            account_id="a-payer",
            transaction_type="escrow_lock",
            amount=100,
            balance_after=0,
            reference="t-1",
        )
        _add_escrow(
            connection,
            escrow_id="esc-1",
            payer_id="a-payer",
            amount=100,
            task_id="t-1",
            status="split",
        )
        leg_amount = 100 // len(release_accounts)
        for index, account_id in enumerate(release_accounts, start=1):
            _add_transaction(
                connection,
                tx_id=f"tx-release-{index}",
                account_id=account_id,
                transaction_type="escrow_release",
                amount=leg_amount,
                balance_after=leg_amount * index,
                reference="esc-1",
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert result.financially_clean is True


@pytest.mark.parametrize(
    ("account_id", "expected_code"),
    [
        (sqlite3.Binary(b"1"), "invalid_identifier_storage"),
        ("", "invalid_identifier_value"),
        (None, "invalid_identifier_storage"),
    ],
)
def test_invalid_identifier_is_never_used_as_identity_key(
    account_id: object, expected_code: str
) -> None:
    connection = _legacy_connection()
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES (?, 10, 'now')",
            (account_id,),
        )
        if account_id is not None:
            connection.execute(
                "INSERT INTO bank_transactions "
                "(tx_id, account_id, type, amount, balance_after, reference, timestamp) "
                "VALUES ('tx-1', ?, 'credit', 10, 10, 'ref', 'now')",
                (account_id,),
            )
        result = _audit(connection)
    finally:
        connection.close()

    assert expected_code in {finding.code for finding in result.findings}
    assert result.account_reconciliations is None
