"""Read-only monetary reconciliation for verified disposable migration clones."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from db_gateway_service.schema import SQLITE_MAX_INTEGER

if TYPE_CHECKING:
    import sqlite3

_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "bank_accounts": ("account_id", "balance"),
    "bank_transactions": (
        "tx_id",
        "account_id",
        "type",
        "amount",
        "balance_after",
        "reference",
    ),
    "bank_escrow": ("escrow_id", "payer_account_id", "amount", "task_id", "status"),
    "board_tasks": ("task_id", "poster_id", "reward", "escrow_id"),
    "board_bids": ("bid_id", "amount"),
}
_TRANSACTION_TYPES = frozenset({"credit", "escrow_lock", "escrow_release"})
_ESCROW_STATES = frozenset({"locked", "released", "split"})


@dataclass(frozen=True, order=True, slots=True)
class MoneyAuditFinding:
    """Stable machine-readable reconciliation finding."""

    code: str
    table: str
    column: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True, order=True, slots=True)
class AccountBalanceReconciliation:
    """One account's stored balance compared with its ledger-derived net."""

    account_id: str
    ledger_net: int
    stored_balance: int
    matches: bool


@dataclass(frozen=True, slots=True)
class MoneyAuditResult:
    """Financial status only; this object never authorizes migration."""

    financially_clean: bool
    financially_eligible: bool
    findings: tuple[MoneyAuditFinding, ...]
    total_credit_issuance: int | None
    recorded_burn: int | None
    total_account_balances: int | None
    total_locked_escrow: int | None
    account_reconciliations: tuple[AccountBalanceReconciliation, ...] | None


def audit_money(connection: sqlite3.Connection) -> MoneyAuditResult:
    """Audit stored money without mutating the connection or database."""
    findings: list[MoneyAuditFinding] = []
    if not _validate_required_columns(connection, findings):
        return _result(
            findings,
            credit_issuance=None,
            recorded_burn=None,
            account_balances=None,
            locked_escrow=None,
            reconciliations=None,
        )

    accounts, account_balances, account_identifiers_valid = _audit_accounts(connection, findings)
    transaction_audit = _audit_transactions(connection, accounts, findings)
    locked_escrow, escrows = _audit_escrows(connection, accounts, findings)
    tasks = _audit_board_money(connection, findings)
    linkages_valid = _audit_row_linkages(
        transaction_audit.records,
        escrows,
        tasks,
        findings,
    )

    reconciliations = _reconcile_accounts(
        accounts,
        transaction_audit.ledger_nets,
        available=(
            account_identifiers_valid
            and linkages_valid
            and account_balances is not None
            and transaction_audit.ledger_nets is not None
        ),
        findings=findings,
    )
    if (
        transaction_audit.credit_issuance is not None
        and account_balances is not None
        and locked_escrow is not None
        and transaction_audit.credit_issuance != account_balances + locked_escrow
    ):
        findings.append(
            MoneyAuditFinding(
                "supply_mismatch",
                "bank_transactions",
                "amount",
                (),
            )
        )

    return _result(
        findings,
        credit_issuance=transaction_audit.credit_issuance,
        recorded_burn=transaction_audit.recorded_burn,
        account_balances=account_balances,
        locked_escrow=locked_escrow,
        reconciliations=reconciliations,
    )


@dataclass(frozen=True, slots=True)
class _TransactionAudit:
    credit_issuance: int | None
    recorded_burn: int | None
    ledger_nets: dict[str, int] | None
    records: tuple[_TransactionRecord, ...]


@dataclass(frozen=True, slots=True)
class _TransactionRecord:
    tx_id: str | None
    account_id: str | None
    transaction_type: str | None
    amount: int | None
    reference: str | None


@dataclass(frozen=True, slots=True)
class _EscrowRecord:
    escrow_id: str | None
    payer_id: str | None
    amount: int | None
    task_id: str | None
    status: str | None


@dataclass(frozen=True, slots=True)
class _TaskRecord:
    task_id: str | None
    poster_id: str | None
    reward: int | None
    escrow_id: str | None


def _validate_required_columns(
    connection: sqlite3.Connection, findings: list[MoneyAuditFinding]
) -> bool:
    complete = True
    for table, required in _REQUIRED_COLUMNS.items():
        quoted = _quote_identifier(table)
        actual = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quoted})").fetchall()  # nosec B608
        }
        for column in required:
            if column in actual:
                continue
            complete = False
            findings.append(MoneyAuditFinding("missing_money_column", table, column, ()))
    return complete


def _audit_accounts(
    connection: sqlite3.Connection,
    findings: list[MoneyAuditFinding],
) -> tuple[dict[str, int | None], int | None, bool]:
    accounts: dict[str, int | None] = {}
    total = 0
    available = True
    identifiers_valid = True
    rows = connection.execute(
        "SELECT typeof(account_id), account_id, typeof(balance), balance "
        "FROM bank_accounts ORDER BY account_id"
    ).fetchall()
    for row in rows:
        diagnostic_id = _record_id(row[1])
        account_id = _identifier(
            table="bank_accounts",
            column="account_id",
            record_ids=(diagnostic_id,),
            storage_type=str(row[0]),
            value=row[1],
            findings=findings,
        )
        balance = _money_value(
            table="bank_accounts",
            column="balance",
            record_ids=(diagnostic_id,),
            storage_type=str(row[2]),
            value=row[3],
            minimum=0,
            findings=findings,
        )
        if account_id is None:
            identifiers_valid = False
        else:
            accounts[account_id] = balance
        if balance is None:
            available = False
        else:
            total += balance
    return accounts, total if available else None, identifiers_valid


def _audit_transactions(
    connection: sqlite3.Connection,
    accounts: dict[str, int | None],
    findings: list[MoneyAuditFinding],
) -> _TransactionAudit:
    credit_issuance = 0
    issuance_available = True
    types_available = True
    ledger_nets = dict.fromkeys(accounts, 0)
    ledger_available = all(balance is not None for balance in accounts.values())
    records: list[_TransactionRecord] = []
    rows = connection.execute(
        "SELECT typeof(tx_id), tx_id, typeof(account_id), account_id, "
        "typeof(type), type, typeof(amount), amount, "
        "typeof(balance_after), balance_after, typeof(reference), reference "
        "FROM bank_transactions ORDER BY tx_id"
    ).fetchall()
    for row in rows:
        diagnostic_tx_id = _record_id(row[1])
        tx_id = _identifier(
            table="bank_transactions",
            column="tx_id",
            record_ids=(diagnostic_tx_id,),
            storage_type=str(row[0]),
            value=row[1],
            findings=findings,
        )
        account_id = _identifier(
            table="bank_transactions",
            column="account_id",
            record_ids=(diagnostic_tx_id, _record_id(row[3])),
            storage_type=str(row[2]),
            value=row[3],
            findings=findings,
        )
        transaction_type = row[5] if row[4] == "text" and isinstance(row[5], str) else None
        if transaction_type not in _TRANSACTION_TYPES:
            findings.append(
                MoneyAuditFinding(
                    "unsupported_transaction_type",
                    "bank_transactions",
                    "type",
                    (diagnostic_tx_id,),
                )
            )
            types_available = False
            issuance_available = False
            ledger_available = False

        amount = _money_value(
            table="bank_transactions",
            column="amount",
            record_ids=(diagnostic_tx_id,),
            storage_type=str(row[6]),
            value=row[7],
            minimum=1,
            findings=findings,
        )
        _money_value(
            table="bank_transactions",
            column="balance_after",
            record_ids=(diagnostic_tx_id,),
            storage_type=str(row[8]),
            value=row[9],
            minimum=0,
            findings=findings,
        )
        if amount is None:
            issuance_available = False
            ledger_available = False
        reference = _identifier(
            table="bank_transactions",
            column="reference",
            record_ids=(diagnostic_tx_id, _record_id(row[11])),
            storage_type=str(row[10]),
            value=row[11],
            findings=findings,
        )
        if tx_id is None or account_id is None or reference is None:
            ledger_available = False
        if account_id is not None and account_id not in accounts:
            findings.append(
                MoneyAuditFinding(
                    "orphan_transaction_account",
                    "bank_transactions",
                    "account_id",
                    (diagnostic_tx_id, account_id),
                )
            )
            ledger_available = False

        records.append(
            _TransactionRecord(
                tx_id,
                account_id,
                transaction_type,
                amount,
                reference,
            )
        )

        if amount is None or transaction_type not in _TRANSACTION_TYPES:
            continue
        if transaction_type == "credit":
            credit_issuance += amount
        if account_id is None or account_id not in ledger_nets:
            continue
        if transaction_type == "escrow_lock":
            ledger_nets[account_id] -= amount
        else:
            ledger_nets[account_id] += amount

    return _TransactionAudit(
        credit_issuance if issuance_available else None,
        0 if types_available else None,
        ledger_nets if ledger_available else None,
        tuple(records),
    )


def _audit_escrows(
    connection: sqlite3.Connection,
    accounts: dict[str, int | None],
    findings: list[MoneyAuditFinding],
) -> tuple[int | None, tuple[_EscrowRecord, ...]]:
    locked_total = 0
    available = True
    lifetime: dict[tuple[str, str], list[str]] = {}
    records: list[_EscrowRecord] = []
    rows = connection.execute(
        "SELECT typeof(escrow_id), escrow_id, typeof(payer_account_id), payer_account_id, "
        "typeof(amount), amount, typeof(task_id), task_id, typeof(status), status "
        "FROM bank_escrow ORDER BY escrow_id"
    ).fetchall()
    for row in rows:
        diagnostic_escrow_id = _record_id(row[1])
        escrow_id = _identifier(
            table="bank_escrow",
            column="escrow_id",
            record_ids=(diagnostic_escrow_id,),
            storage_type=str(row[0]),
            value=row[1],
            findings=findings,
        )
        payer_id = _identifier(
            table="bank_escrow",
            column="payer_account_id",
            record_ids=(diagnostic_escrow_id, _record_id(row[3])),
            storage_type=str(row[2]),
            value=row[3],
            findings=findings,
        )
        task_id = _identifier(
            table="bank_escrow",
            column="task_id",
            record_ids=(diagnostic_escrow_id, _record_id(row[7])),
            storage_type=str(row[6]),
            value=row[7],
            findings=findings,
        )
        if escrow_id is not None and payer_id is not None and task_id is not None:
            lifetime.setdefault((payer_id, task_id), []).append(escrow_id)
        amount = _money_value(
            table="bank_escrow",
            column="amount",
            record_ids=(diagnostic_escrow_id,),
            storage_type=str(row[4]),
            value=row[5],
            minimum=1,
            findings=findings,
        )
        status = row[9] if row[8] == "text" and isinstance(row[9], str) else None
        if status not in _ESCROW_STATES:
            findings.append(
                MoneyAuditFinding(
                    "unknown_escrow_status",
                    "bank_escrow",
                    "status",
                    (diagnostic_escrow_id,),
                )
            )
            available = False
        if amount is None:
            available = False
        elif status == "locked":
            locked_total += amount
        if payer_id is not None and payer_id not in accounts:
            findings.append(
                MoneyAuditFinding(
                    "orphan_escrow_payer_account",
                    "bank_escrow",
                    "payer_account_id",
                    (diagnostic_escrow_id, payer_id),
                )
            )
        records.append(_EscrowRecord(escrow_id, payer_id, amount, task_id, status))

    for (payer_id, task_id), escrow_ids in lifetime.items():
        if len(escrow_ids) > 1:
            findings.append(
                MoneyAuditFinding(
                    "duplicate_lifetime_escrow",
                    "bank_escrow",
                    "payer_account_id,task_id",
                    (payer_id, task_id, *sorted(escrow_ids)),
                )
            )
    return locked_total if available else None, tuple(records)


def _audit_board_money(
    connection: sqlite3.Connection,
    findings: list[MoneyAuditFinding],
) -> tuple[_TaskRecord, ...]:
    tasks: list[_TaskRecord] = []
    task_rows = connection.execute(
        "SELECT typeof(task_id), task_id, typeof(poster_id), poster_id, "
        "typeof(reward), reward, typeof(escrow_id), escrow_id "
        "FROM board_tasks ORDER BY task_id"
    ).fetchall()
    for row in task_rows:
        diagnostic_task_id = _record_id(row[1])
        task_id = _identifier(
            table="board_tasks",
            column="task_id",
            record_ids=(diagnostic_task_id,),
            storage_type=str(row[0]),
            value=row[1],
            findings=findings,
        )
        poster_id = _identifier(
            table="board_tasks",
            column="poster_id",
            record_ids=(diagnostic_task_id, _record_id(row[3])),
            storage_type=str(row[2]),
            value=row[3],
            findings=findings,
        )
        reward = _money_value(
            table="board_tasks",
            column="reward",
            record_ids=(diagnostic_task_id,),
            storage_type=str(row[4]),
            value=row[5],
            minimum=1,
            findings=findings,
        )
        escrow_id = _identifier(
            table="board_tasks",
            column="escrow_id",
            record_ids=(diagnostic_task_id, _record_id(row[7])),
            storage_type=str(row[6]),
            value=row[7],
            findings=findings,
        )
        tasks.append(_TaskRecord(task_id, poster_id, reward, escrow_id))

    bid_rows = connection.execute(
        "SELECT typeof(bid_id), bid_id, typeof(amount), amount FROM board_bids ORDER BY bid_id"
    ).fetchall()
    for row in bid_rows:
        diagnostic_bid_id = _record_id(row[1])
        _identifier(
            table="board_bids",
            column="bid_id",
            record_ids=(diagnostic_bid_id,),
            storage_type=str(row[0]),
            value=row[1],
            findings=findings,
        )
        _money_value(
            table="board_bids",
            column="amount",
            record_ids=(diagnostic_bid_id,),
            storage_type=str(row[2]),
            value=row[3],
            minimum=0,
            findings=findings,
        )
    return tuple(tasks)


def _audit_row_linkages(
    transactions: tuple[_TransactionRecord, ...],
    escrows: tuple[_EscrowRecord, ...],
    tasks: tuple[_TaskRecord, ...],
    findings: list[MoneyAuditFinding],
) -> bool:
    initial_count = len(findings)
    valid = all(
        record.tx_id is not None and record.account_id is not None and record.reference is not None
        for record in transactions
    ) and all(
        escrow.escrow_id is not None and escrow.payer_id is not None and escrow.task_id is not None
        for escrow in escrows
    )
    locks = [
        record
        for record in transactions
        if record.transaction_type == "escrow_lock"
        and record.tx_id is not None
        and record.account_id is not None
        and record.reference is not None
        and record.amount is not None
    ]
    releases = [
        record
        for record in transactions
        if record.transaction_type == "escrow_release"
        and record.tx_id is not None
        and record.reference is not None
        and record.amount is not None
    ]
    valid_escrows = [
        escrow
        for escrow in escrows
        if escrow.escrow_id is not None
        and escrow.payer_id is not None
        and escrow.task_id is not None
        and escrow.amount is not None
        and escrow.status in _ESCROW_STATES
    ]
    escrow_by_id: dict[str, _EscrowRecord] = {}
    for escrow in valid_escrows:
        if escrow.escrow_id is None:
            continue
        escrow_by_id[escrow.escrow_id] = escrow
    _audit_lock_linkages(locks, valid_escrows, findings)
    _audit_release_linkages(releases, valid_escrows, escrow_by_id, findings)
    valid = _audit_task_linkages(tasks, escrow_by_id, findings) and valid
    return valid and len(findings) == initial_count


def _audit_lock_linkages(
    locks: list[_TransactionRecord],
    escrows: list[_EscrowRecord],
    findings: list[MoneyAuditFinding],
) -> None:
    for lock in locks:
        exact = [
            escrow
            for escrow in escrows
            if escrow.payer_id == lock.account_id
            and escrow.task_id == lock.reference
            and escrow.amount == lock.amount
        ]
        if not exact:
            findings.append(
                MoneyAuditFinding(
                    "lock_without_escrow",
                    "bank_transactions",
                    "reference",
                    (lock.tx_id or "<invalid>",),
                )
            )

    for escrow in escrows:
        exact_locks = [
            lock
            for lock in locks
            if lock.account_id == escrow.payer_id
            and lock.reference == escrow.task_id
            and lock.amount == escrow.amount
        ]
        if len(exact_locks) != 1:
            findings.append(
                MoneyAuditFinding(
                    "escrow_lock_count",
                    "bank_escrow",
                    "escrow_id",
                    (
                        escrow.escrow_id or "<invalid>",
                        *(lock.tx_id or "<invalid>" for lock in exact_locks),
                    ),
                )
            )
        if not exact_locks:
            _report_lock_mismatches(escrow, locks, findings)


def _audit_release_linkages(
    releases: list[_TransactionRecord],
    escrows: list[_EscrowRecord],
    escrow_by_id: dict[str, _EscrowRecord],
    findings: list[MoneyAuditFinding],
) -> None:
    releases_by_escrow: dict[str, list[_TransactionRecord]] = {}
    for release in releases:
        if release.reference is None:
            continue
        if release.reference not in escrow_by_id:
            findings.append(
                MoneyAuditFinding(
                    "release_without_escrow",
                    "bank_transactions",
                    "reference",
                    (release.tx_id or "<invalid>", release.reference or "<invalid>"),
                )
            )
            continue
        releases_by_escrow.setdefault(release.reference, []).append(release)

    for escrow in escrows:
        if escrow.escrow_id is None:
            continue
        escrow_releases = releases_by_escrow.get(escrow.escrow_id, [])
        release_ids = tuple(release.tx_id or "<invalid>" for release in escrow_releases)
        if escrow.status == "locked":
            if escrow_releases:
                findings.append(
                    MoneyAuditFinding(
                        "locked_escrow_has_release",
                        "bank_escrow",
                        "status",
                        (escrow.escrow_id or "<invalid>", *release_ids),
                    )
                )
            continue
        expected_counts = {1} if escrow.status == "released" else {1, 2}
        if len(escrow_releases) not in expected_counts:
            findings.append(
                MoneyAuditFinding(
                    "resolved_escrow_release_count",
                    "bank_escrow",
                    "status",
                    (escrow.escrow_id or "<invalid>", *release_ids),
                )
            )
        if (
            escrow.status == "split"
            and len(escrow_releases) == 2
            and all(release.account_id != escrow.payer_id for release in escrow_releases)
        ):
            findings.append(
                MoneyAuditFinding(
                    "split_payer_release_missing",
                    "bank_escrow",
                    "payer_account_id",
                    (escrow.escrow_id, *release_ids),
                )
            )
        release_total = sum(release.amount or 0 for release in escrow_releases)
        if release_total != escrow.amount:
            findings.append(
                MoneyAuditFinding(
                    "resolved_escrow_release_amount",
                    "bank_escrow",
                    "amount",
                    (escrow.escrow_id or "<invalid>", *release_ids),
                )
            )


def _audit_task_linkages(
    tasks: tuple[_TaskRecord, ...],
    escrow_by_id: dict[str, _EscrowRecord],
    findings: list[MoneyAuditFinding],
) -> bool:
    valid = True
    for task in tasks:
        if task.task_id is None or task.poster_id is None or task.escrow_id is None:
            valid = False
            continue
        task_escrow = escrow_by_id.get(task.escrow_id)
        if task_escrow is None:
            findings.append(
                MoneyAuditFinding(
                    "task_escrow_missing",
                    "board_tasks",
                    "escrow_id",
                    (task.task_id, task.escrow_id),
                )
            )
            continue
        if task_escrow.payer_id != task.poster_id:
            findings.append(
                MoneyAuditFinding(
                    "task_escrow_payer_mismatch",
                    "board_tasks",
                    "poster_id",
                    (task.task_id, task.escrow_id),
                )
            )
        if task_escrow.task_id != task.task_id:
            findings.append(
                MoneyAuditFinding(
                    "task_escrow_task_mismatch",
                    "board_tasks",
                    "task_id",
                    (task.task_id, task.escrow_id),
                )
            )
        if task.reward is not None and task_escrow.amount != task.reward:
            findings.append(
                MoneyAuditFinding(
                    "task_escrow_reward_mismatch",
                    "board_tasks",
                    "reward",
                    (task.task_id, task.escrow_id),
                )
            )
    return valid


def _report_lock_mismatches(
    escrow: _EscrowRecord,
    locks: list[_TransactionRecord],
    findings: list[MoneyAuditFinding],
) -> None:
    if (
        escrow.escrow_id is None
        or escrow.payer_id is None
        or escrow.task_id is None
        or escrow.amount is None
    ):
        return
    candidates = {
        "payer": [
            lock
            for lock in locks
            if lock.reference == escrow.task_id
            and lock.amount == escrow.amount
            and lock.account_id != escrow.payer_id
        ],
        "reference": [
            lock
            for lock in locks
            if lock.account_id == escrow.payer_id
            and lock.amount == escrow.amount
            and lock.reference != escrow.task_id
        ],
        "amount": [
            lock
            for lock in locks
            if lock.account_id == escrow.payer_id
            and lock.reference == escrow.task_id
            and lock.amount != escrow.amount
        ],
    }
    for dimension, mismatches in candidates.items():
        if not mismatches:
            continue
        findings.append(
            MoneyAuditFinding(
                f"escrow_lock_{dimension}_mismatch",
                "bank_escrow",
                dimension if dimension != "reference" else "task_id",
                (
                    escrow.escrow_id,
                    *(lock.tx_id or "<invalid>" for lock in mismatches),
                ),
            )
        )


def _reconcile_accounts(
    accounts: dict[str, int | None],
    ledger_nets: dict[str, int] | None,
    *,
    available: bool,
    findings: list[MoneyAuditFinding],
) -> tuple[AccountBalanceReconciliation, ...] | None:
    if not available or ledger_nets is None:
        return None
    reconciliations: list[AccountBalanceReconciliation] = []
    for account_id in sorted(accounts):
        stored_balance = accounts[account_id]
        if stored_balance is None:
            return None
        ledger_net = ledger_nets[account_id]
        matches = ledger_net == stored_balance
        reconciliation = AccountBalanceReconciliation(
            account_id,
            ledger_net,
            stored_balance,
            matches,
        )
        reconciliations.append(reconciliation)
        if not matches:
            findings.append(
                MoneyAuditFinding(
                    "account_ledger_drift",
                    "bank_accounts",
                    "balance",
                    (account_id,),
                )
            )
    return tuple(reconciliations)


def _money_value(
    *,
    table: str,
    column: str,
    record_ids: tuple[str, ...],
    storage_type: str,
    value: object,
    minimum: int,
    findings: list[MoneyAuditFinding],
) -> int | None:
    if storage_type != "integer" or isinstance(value, bool) or not isinstance(value, int):
        findings.append(MoneyAuditFinding("invalid_money_storage", table, column, record_ids))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value < minimum:
                findings.append(MoneyAuditFinding("invalid_money_sign", table, column, record_ids))
            if value > SQLITE_MAX_INTEGER:
                findings.append(MoneyAuditFinding("money_out_of_range", table, column, record_ids))
        return None
    if value < minimum:
        findings.append(MoneyAuditFinding("invalid_money_sign", table, column, record_ids))
        return None
    if value > SQLITE_MAX_INTEGER:
        findings.append(MoneyAuditFinding("money_out_of_range", table, column, record_ids))
        return None
    return value


def _identifier(
    *,
    table: str,
    column: str,
    record_ids: tuple[str, ...],
    storage_type: str,
    value: object,
    findings: list[MoneyAuditFinding],
) -> str | None:
    if storage_type != "text" or not isinstance(value, str):
        findings.append(
            MoneyAuditFinding(
                "invalid_identifier_storage",
                table,
                column,
                record_ids,
            )
        )
        return None
    if not value:
        findings.append(
            MoneyAuditFinding(
                "invalid_identifier_value",
                table,
                column,
                record_ids,
            )
        )
        return None
    return value


def _result(
    findings: list[MoneyAuditFinding],
    *,
    credit_issuance: int | None,
    recorded_burn: int | None,
    account_balances: int | None,
    locked_escrow: int | None,
    reconciliations: tuple[AccountBalanceReconciliation, ...] | None,
) -> MoneyAuditResult:
    stable_findings = tuple(sorted(set(findings)))
    clean = not stable_findings
    return MoneyAuditResult(
        financially_clean=clean,
        financially_eligible=clean,
        findings=stable_findings,
        total_credit_issuance=credit_issuance,
        recorded_burn=recorded_burn,
        total_account_balances=account_balances,
        total_locked_escrow=locked_escrow,
        account_reconciliations=reconciliations,
    )


def _record_id(value: object) -> str:
    if isinstance(value, bytes):
        return value.hex()
    if value is None:
        return "<null>"
    return str(value)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
