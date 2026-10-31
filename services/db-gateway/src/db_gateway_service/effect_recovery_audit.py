"""Read-only audit for MP-08 financial-effect recovery ownership and coverage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from service_commons.exceptions import ServiceError

from db_gateway_service.effect_delivery_requests import parse_worker_id
from db_gateway_service.financial_effect_projection import (
    build_legacy_financial_intent,
    evaluate_financial_receipt,
    evaluate_native_financial_evidence,
)
from db_gateway_service.historical_financial_evidence import (
    build_historical_audit_context,
    classify_historical_worker_payment,
    possible_historical_worker_payment,
)

if TYPE_CHECKING:
    import sqlite3

_EMPTY_MEMBERS_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
_REQUIRED_TABLES = frozenset(
    {
        "board_task_effect_delivery_cutover",
        "board_task_effect_deliveries",
        "board_task_operations",
        "board_legacy_effect_observations",
        "bank_operation_receipts",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class EffectRecoveryAuditFinding:
    """Stable machine-readable recovery finding."""

    code: str
    table: str
    record_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EffectRecoveryAuditResult:
    """Recovery coverage status; this object never authorizes mutation."""

    recovery_clean: bool
    findings: tuple[EffectRecoveryAuditFinding, ...]
    native_source_count: int
    legacy_source_count: int
    delivery_count: int


def audit_effect_recovery(connection: sqlite3.Connection) -> EffectRecoveryAuditResult:
    """Verify frozen migration membership and current delivery coverage."""
    findings: list[EffectRecoveryAuditFinding] = []
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(_REQUIRED_TABLES - tables)
    findings.extend(_finding("missing_effect_recovery_table", table) for table in missing)
    if missing:
        return _result(connection, findings, tables)

    cutover = connection.execute(
        "SELECT cutover_kind,native_member_count,native_members_sha256,"
        "legacy_member_count,legacy_members_sha256,combined_member_count,"
        "combined_members_sha256,caller_kid FROM board_task_effect_delivery_cutover "
        "WHERE singleton=1"
    ).fetchone()
    if cutover is None:
        findings.append(_finding("cutover_missing", "board_task_effect_delivery_cutover"))
        return _result(connection, findings, tables)

    native_members = _native_members(connection)
    legacy_members = _legacy_members(connection)
    combined_members = _combined_members(connection)
    expected = (
        len(native_members),
        _digest(native_members),
        len(legacy_members),
        _digest(legacy_members),
        len(combined_members),
        _digest(combined_members),
    )
    stored = tuple(cutover[1:7])
    labels = ("native", "native", "legacy", "legacy", "combined", "combined")
    for index, label in enumerate(labels):
        if stored[index] != expected[index]:
            findings.append(
                _finding(
                    f"cutover_{label}_membership_mismatch",
                    "board_task_effect_delivery_cutover",
                    "1",
                )
            )
            if label == "legacy":
                findings.append(
                    _finding(
                        "legacy_request_binding_mismatch",
                        "board_legacy_effect_observations",
                    )
                )

    if str(cutover[0]) == "fresh" and stored != (
        0,
        _EMPTY_MEMBERS_SHA256,
        0,
        _EMPTY_MEMBERS_SHA256,
        0,
        _EMPTY_MEMBERS_SHA256,
    ):
        findings.append(
            _finding("fresh_cutover_membership_invalid", "board_task_effect_delivery_cutover")
        )

    _audit_native_coverage(connection, findings)
    _audit_legacy_coverage(
        connection,
        findings,
        cutover_caller_kid=None if cutover[7] is None else str(cutover[7]),
    )
    _audit_delivery_states(connection, findings)
    _audit_delivery_counters(connection, findings)
    _audit_competing_sources(connection, findings)
    _audit_historical_evidence(connection, findings)
    return _result(connection, findings, tables)


def _native_members(connection: sqlite3.Connection) -> list[list[object]]:
    rows = connection.execute(
        "SELECT o.operation_id,o.task_id,o.caller_kid,o.effect_kind,"
        "o.effect_operation_id,o.request_sha256 FROM board_task_operations o "
        "JOIN board_task_effect_deliveries d ON d.native_operation_id=o.operation_id "
        "WHERE d.cutover_singleton=1 AND d.source_kind='native_task_operation'"
    ).fetchall()
    return [list(row) for row in sorted(rows, key=lambda row: str(row[0]).encode("utf-8"))]


def _legacy_members(connection: sqlite3.Connection) -> list[list[object]]:
    rows = connection.execute(
        "SELECT o.observation_id,o.task_id,o.observation_kind,o.external_reference,"
        "o.observed_fact_sha256,d.owner_caller_kid "
        "FROM board_legacy_effect_observations o "
        "JOIN board_task_effect_deliveries d ON d.legacy_observation_id=o.observation_id "
        "WHERE d.cutover_singleton=1 "
        "AND d.source_kind='legacy_settlement_observation'"
    ).fetchall()
    return [list(row) for row in sorted(rows, key=lambda row: str(row[0]).encode("utf-8"))]


def _combined_members(connection: sqlite3.Connection) -> list[list[object]]:
    rows = connection.execute(
        "SELECT source_kind,CASE source_kind WHEN 'native_task_operation' "
        "THEN native_operation_id ELSE legacy_observation_id END,effect_operation_id,"
        "task_id,owner_caller_kid,effect_kind FROM board_task_effect_deliveries "
        "WHERE cutover_singleton=1"
    ).fetchall()
    return [list(row) for row in sorted(rows, key=lambda row: str(row[1]).encode("utf-8"))]


def _audit_native_coverage(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT o.operation_id,o.task_id,o.caller_kid,o.effect_kind,o.effect_operation_id,"
        "d.effect_operation_id,d.task_id,d.source_kind,d.native_operation_id,"
        "d.legacy_observation_id,d.owner_caller_kid,d.effect_kind,d.cutover_singleton,"
        "o.effect_intent_json,d.delivery_state,d.completion_event_id "
        "FROM board_task_operations o LEFT JOIN board_task_effect_deliveries d "
        "ON d.native_operation_id=o.operation_id "
        "WHERE o.effect_kind IN ('bank_release','bank_split') ORDER BY o.operation_id"
    ).fetchall()
    for row in rows:
        operation_id = str(row[0])
        if row[5] is None:
            findings.append(
                _finding("native_delivery_missing", "board_task_operations", operation_id)
            )
            continue
        if row[7] != "native_task_operation" or row[8] != row[0] or row[9] is not None:
            findings.append(
                _finding(
                    "native_delivery_source_mismatch", "board_task_effect_deliveries", operation_id
                )
            )
        if row[6] != row[1]:
            findings.append(
                _finding(
                    "native_delivery_task_mismatch", "board_task_effect_deliveries", operation_id
                )
            )
        if row[10] != row[2]:
            findings.append(
                _finding(
                    "native_delivery_owner_mismatch", "board_task_effect_deliveries", operation_id
                )
            )
        if row[5] != row[4] or row[11] != row[3]:
            findings.append(
                _finding(
                    "native_delivery_effect_mismatch", "board_task_effect_deliveries", operation_id
                )
            )
        if row[12] is not None and row[12] != 1:
            findings.append(
                _finding(
                    "native_delivery_cutover_mismatch", "board_task_effect_deliveries", operation_id
                )
            )
        native = evaluate_native_financial_evidence(connection, operation_id)
        delivery_state = str(row[14])
        if native.receipt.state == "conflict" and delivery_state != "integrity_blocked":
            findings.append(
                _finding(
                    "native_delivery_receipt_conflict",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )
        expected_source = {
            "pending": ("claimed", "pending", "source_current"),
            "leased": ("claimed", "pending", "source_current"),
            "dead_letter": ("claimed", "pending", "source_current"),
            "completed": ("completed", "succeeded", "target_current"),
        }.get(delivery_state)
        if (
            expected_source is not None
            and (
                native.operation_state,
                native.effect_state,
                native.projection_state,
            )
            != expected_source
        ):
            findings.append(
                _finding(
                    "native_delivery_source_state_mismatch",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )
        if delivery_state == "integrity_blocked" and (
            (native.operation_state, native.effect_state) != ("blocked", "conflict")
            or not native.block_reason_valid
        ):
            findings.append(
                _finding(
                    "native_delivery_block_without_conflict",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )
        if delivery_state == "completed" and (
            native.receipt.state != "confirmed" or row[15] != native.receipt.event_id
        ):
            findings.append(
                _finding(
                    "delivery_completion_receipt_mismatch",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )


def _audit_legacy_coverage(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
    *,
    cutover_caller_kid: str | None,
) -> None:
    rows = connection.execute(
        "SELECT o.observation_id,o.task_id,o.observation_kind,o.external_reference,"
        "o.observed_fact_json,o.observed_fact_sha256,d.effect_operation_id,d.task_id,"
        "d.source_kind,d.native_operation_id,d.legacy_observation_id,d.owner_caller_kid,"
        "d.effect_kind,d.cutover_singleton,o.unresolved,d.delivery_state,"
        "d.completion_event_id FROM board_legacy_effect_observations o "
        "LEFT JOIN board_task_effect_deliveries d ON d.legacy_observation_id=o.observation_id "
        "WHERE o.observation_kind IN "
        "('worker_settlement_pending','worker_settlement_succeeded') "
        "AND (o.unresolved=1 OR d.cutover_singleton=1) "
        "ORDER BY o.observation_id"
    ).fetchall()
    for row in rows:
        observation_id = str(row[0])
        if row[6] is None:
            findings.append(
                _finding(
                    "legacy_delivery_missing", "board_legacy_effect_observations", observation_id
                )
            )
            continue
        if _sha256(str(row[4])) != row[5]:
            findings.append(
                _finding(
                    "legacy_request_binding_mismatch",
                    "board_legacy_effect_observations",
                    observation_id,
                )
            )
        if row[8] != "legacy_settlement_observation" or row[9] is not None or row[10] != row[0]:
            findings.append(
                _finding(
                    "legacy_delivery_source_mismatch",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
        if row[7] != row[1]:
            findings.append(
                _finding(
                    "legacy_delivery_task_mismatch", "board_task_effect_deliveries", observation_id
                )
            )
        if row[6] != row[3] or row[12] != "bank_release":
            findings.append(
                _finding(
                    "legacy_delivery_effect_mismatch",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
        if row[13] != 1:
            findings.append(
                _finding(
                    "legacy_delivery_cutover_mismatch",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
        if not isinstance(row[11], str) or not row[11] or row[11] != cutover_caller_kid:
            findings.append(
                _finding(
                    "legacy_delivery_owner_mismatch", "board_task_effect_deliveries", observation_id
                )
            )
        task = _task_mapping(connection, str(row[1]))
        intent = build_legacy_financial_intent(
            (row[2], row[4], row[5], row[3], row[14]),
            task,
        )
        evidence = evaluate_financial_receipt(connection, str(row[3]), intent)
        if intent is None or evidence.state == "conflict":
            findings.append(
                _finding(
                    "legacy_delivery_receipt_conflict",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
        if row[2] == "worker_settlement_succeeded" and evidence.state != "confirmed":
            findings.append(
                _finding(
                    "legacy_succeeded_receipt_missing",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
        if row[15] == "completed" and (
            evidence.state != "confirmed" or row[16] != evidence.event_id
        ):
            findings.append(
                _finding(
                    "delivery_completion_receipt_mismatch",
                    "board_task_effect_deliveries",
                    observation_id,
                )
            )
    orphan_rows = connection.execute(
        "SELECT d.effect_operation_id,d.legacy_observation_id "
        "FROM board_task_effect_deliveries d "
        "LEFT JOIN board_legacy_effect_observations o "
        "ON o.observation_id=d.legacy_observation_id "
        "WHERE d.source_kind='legacy_settlement_observation' AND o.observation_id IS NULL"
    ).fetchall()
    findings.extend(
        _finding(
            "legacy_delivery_source_mismatch",
            "board_task_effect_deliveries",
            str(row[0]),
        )
        for row in orphan_rows
    )


def _audit_delivery_states(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT effect_operation_id,delivery_state,enqueued_at_us,next_attempt_at_us,"
        "lease_owner_id,lease_acquired_at_us,lease_expires_at_us,last_failure_code,"
        "last_failure_at_us,next_evidence_check_at_us,completed_at_us,completion_event_id "
        "FROM board_task_effect_deliveries ORDER BY effect_operation_id"
    ).fetchall()
    for row in rows:
        operation_id = str(row[0])
        state = str(row[1])
        enqueued = row[2]
        state_valid = _state_fields_valid(tuple(row[1:]))
        if not state_valid:
            findings.append(
                _finding(
                    "delivery_state_binding_invalid", "board_task_effect_deliveries", operation_id
                )
            )
        if not _temporal_fields_valid(enqueued, row[3], row[5], row[6], row[8], row[9], row[10]):
            findings.append(
                _finding(
                    "delivery_temporal_binding_invalid",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )
        if state == "leased":
            try:
                parse_worker_id(row[4])
            except ServiceError:
                findings.append(
                    _finding(
                        "delivery_worker_identity_invalid",
                        "board_task_effect_deliveries",
                        operation_id,
                    )
                )
        if state == "completed":
            receipt = connection.execute(
                "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if receipt is None or row[11] != receipt[0]:
                findings.append(
                    _finding(
                        "delivery_completion_receipt_mismatch",
                        "board_task_effect_deliveries",
                        operation_id,
                    )
                )


def _task_mapping(connection: sqlite3.Connection, task_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT task_id,poster_id,worker_id,escrow_id,reward,status "
        "FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        return {
            "task_id": task_id,
            "poster_id": None,
            "worker_id": None,
            "escrow_id": None,
            "reward": None,
            "status": None,
        }
    return {
        "task_id": row[0],
        "poster_id": row[1],
        "worker_id": row[2],
        "escrow_id": row[3],
        "reward": row[4],
        "status": row[5],
    }


def _state_fields_valid(row: tuple[object, ...]) -> bool:
    (
        state,
        _enqueued,
        next_attempt,
        owner,
        acquired,
        expires,
        failure,
        failed_at,
        evidence_at,
        completed,
        event,
    ) = row
    failure_pair = (failure is None) == (failed_at is None)
    if not failure_pair:
        return False
    if state == "pending":
        valid = next_attempt is not None and all(
            value is None for value in (owner, acquired, expires, evidence_at, completed, event)
        )
    elif state == "leased":
        valid = (
            next_attempt is None
            and all(value is not None for value in (owner, acquired, expires))
            and all(value is None for value in (evidence_at, completed, event))
        )
    elif state == "dead_letter":
        valid = (
            next_attempt is None
            and evidence_at is not None
            and failure is not None
            and all(value is None for value in (owner, acquired, expires, completed, event))
        )
    elif state == "integrity_blocked":
        valid = (
            failure == "effect_evidence_conflict"
            and next_attempt is None
            and all(
                value is None for value in (owner, acquired, expires, evidence_at, completed, event)
            )
        )
    elif state == "completed":
        valid = (
            completed is not None
            and event is not None
            and all(
                value is None for value in (next_attempt, owner, acquired, expires, evidence_at)
            )
        )
    else:
        valid = False
    return valid


def _temporal_fields_valid(enqueued: object, *values: object) -> bool:
    if type(enqueued) is not int or not 0 <= enqueued <= 253402300799999999:
        return False
    present = [value for value in values if value is not None]
    if any(
        type(value) is not int or not enqueued <= value <= 253402300799999999 for value in present
    ):
        return False
    acquired, expires = cast("tuple[int | None, int | None]", values[1:3])
    return acquired is None or expires is None or expires > acquired


def _audit_competing_sources(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
) -> None:
    native_tasks = {
        str(row[0])
        for row in connection.execute(
            "SELECT task_id FROM board_task_operations "
            "WHERE effect_kind IN ('bank_release','bank_split')"
        ).fetchall()
    }
    legacy_tasks = {
        str(row[0])
        for row in connection.execute(
            "SELECT task_id FROM board_legacy_effect_observations WHERE unresolved=1 "
            "AND observation_kind IN "
            "('worker_settlement_pending','worker_settlement_succeeded')"
        ).fetchall()
    }
    for task_id in sorted(native_tasks & legacy_tasks):
        findings.append(_finding("competing_effect_sources", "board_tasks", task_id))


def _audit_historical_evidence(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT t.task_id,t.poster_id,t.worker_id,t.escrow_id,t.reward,t.status,"
        "t.approved_at,t.accepted_bid_id FROM board_tasks t WHERE t.status='approved' "
        "AND NOT EXISTS (SELECT 1 FROM board_task_operations o WHERE o.task_id=t.task_id "
        "AND o.effect_kind IN ('bank_release','bank_split')) "
        "AND NOT EXISTS (SELECT 1 FROM board_task_effect_deliveries d "
        "WHERE d.task_id=t.task_id) ORDER BY t.task_id"
    ).fetchall()
    tasks = [
        {
            "task_id": row[0],
            "poster_id": row[1],
            "worker_id": row[2],
            "escrow_id": row[3],
            "reward": row[4],
            "status": row[5],
            "approved_at": row[6],
            "accepted_bid_id": row[7],
        }
        for row in rows
    ]
    candidates = [task for task in tasks if possible_historical_worker_payment(connection, task)]
    if not candidates:
        return
    context = build_historical_audit_context(connection)
    for task in candidates:
        evidence = classify_historical_worker_payment(connection, task, context)
        if evidence.evidence_present and not evidence.qualifies:
            findings.append(
                _finding(
                    "historical_payment_evidence_invalid",
                    "board_tasks",
                    str(task["task_id"]),
                )
            )


def _audit_delivery_counters(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT effect_operation_id,total_attempt_count,cycle_attempt_count,"
        "lease_generation,operator_retry_count,last_operator_retry_at_us,"
        "last_operator_retry_caller_kid,enqueued_at_us "
        "FROM board_task_effect_deliveries ORDER BY effect_operation_id"
    ).fetchall()
    for row in rows:
        operation_id = str(row[0])
        total, cycle, generation, retries = row[1:5]
        counters_valid = (
            type(total) is int
            and type(cycle) is int
            and type(generation) is int
            and type(retries) is int
            and 0 <= cycle <= total <= 9223372036854775807
            and 0 <= generation <= 9223372036854775807
            and 0 <= retries <= 9223372036854775807
        )
        if not counters_valid:
            findings.append(
                _finding(
                    "delivery_counter_binding_invalid",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )
        retry_fields_valid = (retries == 0 and row[5] is None and row[6] is None) or (
            type(retries) is int
            and retries > 0
            and type(row[5]) is int
            and isinstance(row[6], str)
            and bool(row[6])
            and type(row[7]) is int
            and row[7] <= row[5] <= 253402300799999999
        )
        if not retry_fields_valid:
            findings.append(
                _finding(
                    "delivery_operator_retry_binding_invalid",
                    "board_task_effect_deliveries",
                    operation_id,
                )
            )


def _result(
    connection: sqlite3.Connection,
    findings: list[EffectRecoveryAuditFinding],
    tables: set[str],
) -> EffectRecoveryAuditResult:
    def count(table: str, where: str) -> int:
        if table not in tables:
            return 0
        row = connection.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()  # nosec B608
        return int(row[0]) if row is not None else 0

    ordered = tuple(sorted(set(findings)))
    return EffectRecoveryAuditResult(
        recovery_clean=not ordered,
        findings=ordered,
        native_source_count=count(
            "board_task_operations", "WHERE effect_kind IN ('bank_release','bank_split')"
        ),
        legacy_source_count=count(
            "board_legacy_effect_observations",
            "WHERE observation_kind IN ('worker_settlement_pending','worker_settlement_succeeded')",
        ),
        delivery_count=count("board_task_effect_deliveries", ""),
    )


def _finding(code: str, table: str, *record_ids: str) -> EffectRecoveryAuditFinding:
    return EffectRecoveryAuditFinding(code, table, tuple(record_ids))


def _digest(value: list[list[object]]) -> str:
    return _sha256(json.dumps(value, separators=(",", ":"), ensure_ascii=False))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = ["EffectRecoveryAuditFinding", "EffectRecoveryAuditResult", "audit_effect_recovery"]
