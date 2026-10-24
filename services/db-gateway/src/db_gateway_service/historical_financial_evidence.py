"""Pure exhaustive classifier for pre-receipt historical worker payments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from db_gateway_service.receipt_audit import (
    audit_receipt_ownership,
    legacy_marker_set_attested,
)
from db_gateway_service.task_contract_audit import audit_task_contracts

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class HistoricalPaymentEvidence:
    """Classification plus stable reasons for one possible historical payment."""

    qualifies: bool
    evidence_present: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HistoricalAuditContext:
    """One read's immutable global audit facts; never retained across reads."""

    receipt_ownership_clean: bool
    financially_clean: bool
    legacy_marker_set_attested: bool
    task_contracts_clean: bool


def build_historical_audit_context(
    connection: sqlite3.Connection,
) -> HistoricalAuditContext:
    """Compute global historical prerequisites once for one synchronous read."""
    receipt = audit_receipt_ownership(connection)
    return HistoricalAuditContext(
        receipt_ownership_clean=receipt.ownership_clean,
        financially_clean=receipt.financially_clean,
        legacy_marker_set_attested=legacy_marker_set_attested(connection),
        task_contracts_clean=audit_task_contracts(connection).contracts_clean,
    )


def possible_historical_worker_payment(
    connection: sqlite3.Connection,
    task: Mapping[str, object],
) -> bool:
    """Cheaply reject ordinary tasks before any repository-wide financial audit."""
    if (
        str(task["status"]) != "approved"
        or task.get("approved_at") is None
        or not isinstance(task.get("worker_id"), str)
    ):
        return False
    escrow_id = str(task["escrow_id"])
    return (
        connection.execute(
            "SELECT 1 FROM bank_legacy_escrow_observations WHERE escrow_id=? "
            "AND observed_status='released'",
            (escrow_id,),
        ).fetchone()
        is not None
    )


def classify_historical_worker_payment(
    connection: sqlite3.Connection,
    task: Mapping[str, object],
    audit_context: HistoricalAuditContext,
) -> HistoricalPaymentEvidence:
    """Prove a pre-receipt worker payment from complete attested legacy evidence."""
    escrow_id = str(task["escrow_id"])
    markers = connection.execute(
        "SELECT observed_status FROM bank_legacy_escrow_observations WHERE escrow_id=?",
        (escrow_id,),
    ).fetchall()
    transactions = connection.execute(
        "SELECT t.tx_id,t.account_id,t.type,t.amount,t.balance_after,t.reference,t.timestamp,"
        "t.event_id,o.observed_kind,e.event_source,e.event_type,e.task_id "
        "FROM bank_transactions t JOIN bank_legacy_transaction_observations o "
        "ON o.tx_id=t.tx_id LEFT JOIN events e ON e.event_id=t.event_id "
        "WHERE t.reference=? ORDER BY t.tx_id",
        (escrow_id,),
    ).fetchall()
    evidence_present = bool(markers or transactions)
    reasons = _task_and_source_reasons(connection, task)
    reasons.extend(_legacy_fact_reasons(task, escrow_id, markers, transactions, connection))
    reasons.extend(_audit_reasons(audit_context))
    return HistoricalPaymentEvidence(
        qualifies=evidence_present and not reasons,
        evidence_present=evidence_present,
        reason_codes=tuple(sorted(set(reasons))),
    )


def _task_and_source_reasons(
    connection: sqlite3.Connection,
    task: Mapping[str, object],
) -> list[str]:
    task_id = str(task["task_id"])
    escrow_id = str(task["escrow_id"])
    if str(task["status"]) != "approved" or task.get("approved_at") is None:
        reasons = ["task_not_approved"]
    else:
        reasons = []
    worker_id = task["worker_id"]
    reward = task["reward"]
    if not isinstance(worker_id, str) or not worker_id:
        reasons.append("worker_missing")
    if type(reward) is not int or reward <= 0:
        reasons.append("reward_invalid")
    if (
        connection.execute(
            "SELECT 1 FROM board_task_operations WHERE task_id=? AND effect_kind IN "
            "('bank_release','bank_split')",
            (task_id,),
        ).fetchone()
        is not None
    ):
        reasons.append("native_source_present")
    if (
        connection.execute(
            "SELECT 1 FROM board_task_effect_deliveries WHERE task_id=?",
            (task_id,),
        ).fetchone()
        is not None
    ):
        reasons.append("delivery_present")
    if (
        connection.execute(
            "SELECT 1 FROM bank_operation_receipts WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        is not None
    ):
        reasons.append("native_receipt_present")
    return reasons


def _legacy_fact_reasons(
    task: Mapping[str, object],
    escrow_id: str,
    markers: Sequence[Sequence[object]],
    transactions: Sequence[Sequence[object]],
    connection: sqlite3.Connection,
) -> list[str]:
    reasons: list[str] = []
    task_id = str(task["task_id"])
    worker_id = task["worker_id"]
    reward = task["reward"]
    escrow = connection.execute(
        "SELECT payer_account_id,amount,task_id,status,resolved_at FROM bank_escrow "
        "WHERE escrow_id=?",
        (escrow_id,),
    ).fetchone()
    if (
        escrow is None
        or tuple(escrow[:4])
        != (
            task["poster_id"],
            reward,
            task_id,
            "released",
        )
        or escrow[4] is None
    ):
        reasons.append("escrow_not_exact")
    if len(markers) != 1 or str(markers[0][0]) != "released":
        reasons.append("legacy_escrow_marker_not_exact")
    expected_transaction = (
        worker_id,
        "escrow_release",
        reward,
        escrow_id,
        "observed_escrow_release",
        "central-bank",
        "escrow.released",
        task_id,
    )
    if (
        len(transactions) != 1
        or (
            transactions[0][1],
            transactions[0][2],
            transactions[0][3],
            transactions[0][5],
            transactions[0][8],
            transactions[0][9],
            transactions[0][10],
            transactions[0][11],
        )
        != expected_transaction
    ):
        reasons.append("legacy_worker_transaction_not_exact")
    elif escrow is not None and transactions[0][6] != escrow[4]:
        reasons.append("settlement_timestamp_mismatch")
    contract = connection.execute(
        "SELECT accepted_reward,accepted_bid_amount FROM board_task_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    bid = connection.execute(
        "SELECT bidder_id,amount FROM board_bids WHERE bid_id=? AND task_id=?",
        (task.get("accepted_bid_id"), task_id),
    ).fetchone()
    if (
        contract is None
        or contract[0] != reward
        or bid is None
        or bid[0] != worker_id
        or bid[1] != contract[1]
    ):
        reasons.append("accepted_terms_not_exact")
    return reasons


def _audit_reasons(context: HistoricalAuditContext) -> list[str]:
    reasons: list[str] = []
    if not context.receipt_ownership_clean or not context.financially_clean:
        reasons.append("receipt_or_money_audit_failed")
    if not context.legacy_marker_set_attested:
        reasons.append("legacy_cutover_not_attested")
    if not context.task_contracts_clean:
        reasons.append("task_contract_audit_failed")
    return reasons


__all__ = [
    "HistoricalAuditContext",
    "HistoricalPaymentEvidence",
    "build_historical_audit_context",
    "classify_historical_worker_payment",
    "possible_historical_worker_payment",
]
