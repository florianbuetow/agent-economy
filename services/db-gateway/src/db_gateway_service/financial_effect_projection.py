"""Evidence-derived public task settlement projection for MP-08."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.historical_financial_evidence import (
    HistoricalAuditContext,
    build_historical_audit_context,
    classify_historical_worker_payment,
    possible_historical_worker_payment,
)
from db_gateway_service.services.task_operation_writer import TaskOperationWriter

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class FinancialReceiptEvidence:
    """Classification of persisted evidence for one immutable financial intent."""

    state: str
    outcome: str | None
    receipt_reference: str | None
    event_id: int | None


@dataclass(frozen=True, slots=True)
class NativeFinancialEvidence:
    """Receipt and business-projection coherence for one native effect source."""

    receipt: FinancialReceiptEvidence
    operation_state: str | None
    effect_state: str | None
    projection_state: str
    source_pair_valid: bool
    block_reason_valid: bool


def evaluate_native_financial_evidence(
    connection: sqlite3.Connection,
    operation_id: str,
) -> NativeFinancialEvidence:
    """Classify receipt validity separately from source/task projection coherence."""
    row = connection.execute(
        "SELECT o.operation_state,o.effect_state,o.source_status,o.source_lifecycle_version,"
        "o.target_status,o.target_lifecycle_version,o.effect_intent_json,t.status,"
        "c.lifecycle_version FROM board_task_operations o "
        "LEFT JOIN board_tasks t ON t.task_id=o.task_id "
        "LEFT JOIN board_task_contracts c ON c.task_id=o.task_id WHERE o.operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        missing = FinancialReceiptEvidence("conflict", None, None, None)
        return NativeFinancialEvidence(missing, None, None, "contradictory", False, False)
    try:
        intent = json.loads(str(row[6]))
    except (TypeError, ValueError):
        intent = None
    receipt = evaluate_financial_receipt(connection, operation_id, intent)
    source_current = row[7] == row[2] and row[8] == row[3]
    target_current = row[7] == row[4] and row[8] == row[5]
    projection_state = (
        "source_current"
        if source_current
        else "target_current"
        if target_current
        else "contradictory"
    )
    operation_state, effect_state = str(row[0]), str(row[1])
    source_pair_valid = (operation_state, effect_state) in {
        ("claimed", "pending"),
        ("completed", "succeeded"),
        ("blocked", "conflict"),
    }
    block_reason_valid = receipt.state == "conflict" or (
        receipt.state == "confirmed" and projection_state == "contradictory"
    )
    return NativeFinancialEvidence(
        receipt,
        operation_state,
        effect_state,
        projection_state,
        source_pair_valid,
        block_reason_valid,
    )


def financial_effect_for_task(
    connection: sqlite3.Connection,
    task: Mapping[str, object],
    historical_audit_context: HistoricalAuditContext | None,
) -> dict[str, object]:
    """Return the exact public eight-field financial truth for one task."""
    task_id = str(task["task_id"])
    delivery = connection.execute(
        "SELECT effect_operation_id,source_kind,native_operation_id,legacy_observation_id,"
        "delivery_state FROM board_task_effect_deliveries WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if delivery is None:
        historical = False
        if possible_historical_worker_payment(connection, task):
            context = (
                historical_audit_context
                if historical_audit_context is not None
                else build_historical_audit_context(connection)
            )
            historical = classify_historical_worker_payment(
                connection,
                task,
                context,
            ).qualifies
        if historical:
            return _projection(
                settlement_state="historical",
                receipt_state="historical",
                outcome="worker_paid",
                operation_id=None,
                receipt_reference=None,
                business_projection_pending=False,
                pending_target_status=None,
            )
        if str(task["status"]) in {"approved", "ruled", "cancelled", "expired"}:
            return _projection(
                settlement_state="attention_required",
                receipt_state="absent",
                outcome=None,
                operation_id=None,
                receipt_reference=None,
                business_projection_pending=False,
                pending_target_status=None,
            )
        return _projection(
            settlement_state="locked",
            receipt_state="absent",
            outcome=None,
            operation_id=None,
            receipt_reference=None,
            business_projection_pending=False,
            pending_target_status=None,
        )

    operation_id = str(delivery[0])
    state = str(delivery[4])
    if str(delivery[1]) == "native_task_operation":
        evidence = _native_evidence(connection, str(delivery[2]))
    else:
        evidence = _legacy_evidence(connection, str(delivery[3]), task)

    receipt_state, outcome, reference, projection_pending, target, contradictory = evidence
    if receipt_state == "confirmed":
        settlement = (
            "conflict"
            if contradictory or state == "integrity_blocked"
            else ("pending" if projection_pending else "complete")
        )
        return _projection(
            settlement_state=settlement,
            receipt_state="confirmed",
            outcome=outcome,
            operation_id=operation_id,
            receipt_reference=reference,
            business_projection_pending=projection_pending and settlement == "pending",
            pending_target_status=(
                target if projection_pending and settlement == "pending" else None
            ),
        )
    if contradictory or state == "integrity_blocked":
        return _projection(
            settlement_state="conflict",
            receipt_state="absent",
            outcome=None,
            operation_id=operation_id,
            receipt_reference=None,
            business_projection_pending=False,
            pending_target_status=None,
        )
    return _projection(
        settlement_state="attention_required" if state == "dead_letter" else "pending",
        receipt_state="absent",
        outcome=None,
        operation_id=operation_id,
        receipt_reference=None,
        business_projection_pending=projection_pending,
        pending_target_status=target if projection_pending else None,
    )


def _projection(
    *,
    settlement_state: str,
    receipt_state: str,
    outcome: str | None,
    operation_id: str | None,
    receipt_reference: str | None,
    business_projection_pending: bool,
    pending_target_status: str | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "settlement_state": settlement_state,
        "receipt_state": receipt_state,
        "outcome": outcome,
        "operation_id": operation_id,
        "receipt_reference": receipt_reference,
        "business_projection_pending": business_projection_pending,
        "pending_target_status": pending_target_status,
    }


def _native_evidence(
    connection: sqlite3.Connection,
    native_operation_id: str,
) -> tuple[str, str | None, str | None, bool, str | None, bool]:
    evidence = evaluate_native_financial_evidence(connection, native_operation_id)
    operation = connection.execute(
        "SELECT target_status FROM board_task_operations WHERE operation_id=?",
        (native_operation_id,),
    ).fetchone()
    target_status = None if operation is None else str(operation[0])
    pending = (
        evidence.operation_state == "claimed" and evidence.projection_state == "source_current"
    )
    contradictory = evidence.projection_state == "contradictory"
    if evidence.receipt.state == "confirmed":
        return (
            evidence.receipt.state,
            evidence.receipt.outcome,
            evidence.receipt.receipt_reference,
            pending,
            target_status if pending else None,
            contradictory,
        )
    if evidence.receipt.state == "conflict":
        return "absent", None, None, pending, target_status if pending else None, True
    return "absent", None, None, pending, target_status if pending else None, contradictory


def _legacy_evidence(
    connection: sqlite3.Connection,
    observation_id: str,
    task: Mapping[str, object],
) -> tuple[str, str | None, str | None, bool, str | None, bool]:
    observation = connection.execute(
        "SELECT observation_kind,observed_fact_json,observed_fact_sha256,external_reference,"
        "unresolved FROM board_legacy_effect_observations WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    if observation is None:
        return "absent", None, None, False, None, True
    intent = build_legacy_financial_intent(tuple(observation), task)
    if intent is None:
        return "absent", None, None, False, None, True
    evidence = evaluate_financial_receipt(connection, str(observation[3]), intent)
    kind = str(observation[0])
    unresolved = int(observation[4]) == 1
    if kind == "worker_settlement_pending":
        coherent = str(task["status"]) == "approved"
        pending = False
        target = None
    else:
        coherent = str(task["status"]) in {"submitted", "approved"}
        pending = unresolved and str(task["status"]) != "approved"
        target = "approved" if pending else None
    if evidence.state == "confirmed":
        return (
            evidence.state,
            evidence.outcome,
            evidence.receipt_reference,
            pending,
            target,
            not coherent,
        )
    if evidence.state == "conflict":
        return "absent", None, None, pending, target, True
    return "absent", None, None, pending, target, not coherent


def evaluate_financial_receipt(
    connection: sqlite3.Connection,
    operation_id: str,
    intent: object,
) -> FinancialReceiptEvidence:
    """Validate one exact stored receipt without mutating or trusting task status."""
    if not isinstance(intent, dict):
        return FinancialReceiptEvidence("conflict", None, None, None)
    request = intent.get("financial_request")
    request_hash = intent.get("financial_request_sha256", intent.get("request_sha256"))
    expected_escrow = intent.get("expected_escrow")
    if (
        not isinstance(request, dict)
        or not isinstance(request_hash, str)
        or not isinstance(expected_escrow, dict)
    ):
        return FinancialReceiptEvidence("conflict", None, None, None)
    receipt = connection.execute(
        "SELECT receipt_version,operation_kind,canonical_request_json,request_sha256,"
        "source_reference,escrow_id,result_json,event_id,committed_at "
        "FROM bank_operation_receipts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if receipt is None:
        winner = connection.execute(
            "SELECT operation_id FROM bank_operation_receipts WHERE escrow_id=? "
            "AND operation_kind IN ('escrow_release','escrow_split')",
            (str(request.get("escrow_id")),),
        ).fetchone()
        escrow = connection.execute(
            "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
            (str(request.get("escrow_id")),),
        ).fetchone()
        if (
            winner is not None
            or escrow is None
            or str(escrow[0]) != "locked"
            or escrow[1] is not None
        ):
            return FinancialReceiptEvidence("conflict", None, None, None)
        return FinancialReceiptEvidence("absent", None, None, None)
    if not TaskOperationWriter._receipt_matches(
        connection.cursor(),
        operation_id,
        tuple(receipt),
        request,
        request_hash,
        expected_escrow,
    ):
        return FinancialReceiptEvidence("conflict", None, None, None)
    return FinancialReceiptEvidence(
        "confirmed",
        _receipt_outcome(request, expected_escrow),
        operation_id,
        int(receipt[7]),
    )


def _receipt_outcome(request: Mapping[object, object], escrow: Mapping[object, object]) -> str:
    if request.get("operation_kind") == "escrow_split":
        return "split"
    recipient = request.get("recipient_account_id")
    return "poster_refunded" if recipient == escrow.get("payer_account_id") else "worker_paid"


def build_legacy_financial_intent(
    observation: tuple[object, ...],
    task: Mapping[str, object],
) -> dict[str, object] | None:
    """Derive the one immutable release intent from a migrated observation."""
    try:
        fact = json.loads(str(observation[1]))
    except (TypeError, ValueError):
        return None
    canonical = json.dumps(
        fact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if (
        not isinstance(fact, dict)
        or hashlib.sha256(canonical.encode()).hexdigest() != observation[2]
    ):
        return None
    reward = task["reward"]
    if type(reward) is not int:
        return None
    required = {
        "task_id": task["task_id"],
        "poster_id": task["poster_id"],
        "worker_id": task["worker_id"],
        "escrow_id": task["escrow_id"],
        "reward": task["reward"],
        "external_reference": observation[3],
    }
    if any(fact.get(key) != value for key, value in required.items()):
        return None
    request = canonical_request(
        "escrow_release",
        {
            "escrow_id": str(task["escrow_id"]),
            "recipient_account_id": str(task["worker_id"]),
            "source_reference": f"task:{task['task_id']}:approved",
        },
    )
    return {
        "financial_request": json.loads(request.canonical_json),
        "financial_request_sha256": request.request_sha256,
        "expected_escrow": {
            "escrow_id": str(task["escrow_id"]),
            "task_id": str(task["task_id"]),
            "payer_account_id": str(task["poster_id"]),
            "amount": reward,
        },
    }


__all__ = [
    "FinancialReceiptEvidence",
    "NativeFinancialEvidence",
    "build_legacy_financial_intent",
    "evaluate_financial_receipt",
    "evaluate_native_financial_evidence",
    "financial_effect_for_task",
]
