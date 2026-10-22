"""Read-only integrity audit for durable native publication and legacy cutover state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request, parse_canonical_request
from db_gateway_service.task_publication_financial import (
    PublicationEvidenceError,
    inspect_lock_facts,
)
from db_gateway_service.task_publication_requests import (
    TaskPublicationRequest,
    parse_task_publication_request,
)
from db_gateway_service.task_time import (
    add_seconds_to_epoch_us,
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)

if TYPE_CHECKING:
    import sqlite3

_REQUIRED_TABLES = frozenset(
    {
        "identity_agents",
        "board_task_publications",
        "board_publication_cutover",
        "board_legacy_posting_observations",
    }
)
_BLOCK_REASONS = frozenset(
    {
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    }
)
_LEGACY_BLOCK_REASONS = frozenset(
    {
        "observation_invalid",
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "task_escrow_reference_conflict",
    }
)


@dataclass(frozen=True, order=True, slots=True)
class PublicationAuditFinding:
    """Stable machine-readable publication audit finding."""

    code: str
    identity: str


@dataclass(frozen=True, slots=True)
class PublicationAuditResult:
    """Publication integrity status; startup decides whether it is admissible."""

    publications_clean: bool
    findings: tuple[PublicationAuditFinding, ...]
    publication_count: int
    legacy_observation_count: int


def audit_publications(connection: sqlite3.Connection) -> PublicationAuditResult:
    """Audit v4 publication state without mutating rows or persistent PRAGMAs."""
    findings: list[PublicationAuditFinding] = []
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in sorted(_REQUIRED_TABLES - tables):
        findings.append(PublicationAuditFinding("missing_publication_table", table))
    if findings:
        return _result(connection, findings, tables)
    cutover = _audit_cutover(connection, findings)
    _audit_operation_partition(connection, findings)
    _audit_publication_rows(connection, findings)
    _audit_observation_rows(connection, cutover, findings)
    _audit_attested_sets(connection, cutover, findings)
    return _result(connection, findings, tables)


def _audit_cutover(
    connection: sqlite3.Connection,
    findings: list[PublicationAuditFinding],
) -> tuple[object, ...] | None:
    rows = connection.execute(
        "SELECT singleton,cutover_version,cutover_kind,source_schema_version,"
        "source_snapshot_sha256,migration_plan_sha256,backup_id,backup_manifest_sha256,"
        "caller_kid,legacy_task_ids_sha256,legacy_task_count,legacy_observations_sha256,"
        "legacy_observation_count FROM board_publication_cutover ORDER BY singleton"
    ).fetchall()
    if len(rows) != 1:
        findings.append(PublicationAuditFinding("publication_cutover_row_count", str(len(rows))))
        return None
    row = tuple(rows[0])
    if row[:3] not in {(1, 1, "fresh"), (1, 1, "migrated")}:
        findings.append(PublicationAuditFinding("publication_cutover_invalid", "1"))
    if row[2] == "fresh":
        if any(value is not None for value in row[3:9]):
            findings.append(PublicationAuditFinding("publication_cutover_invalid", "1"))
    elif row[3] != 3 or any(not isinstance(value, str) or not value for value in row[4:9]):
        findings.append(PublicationAuditFinding("publication_cutover_invalid", "1"))
    return row


def _audit_operation_partition(
    connection: sqlite3.Connection,
    findings: list[PublicationAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT p.operation_id FROM board_task_publications p "
        "JOIN board_task_operations o ON o.operation_id=p.operation_id "
        "ORDER BY p.operation_id"
    ).fetchall()
    findings.extend(
        PublicationAuditFinding("publication_operation_id_reused", str(row[0])) for row in rows
    )


def _audit_publication_rows(
    connection: sqlite3.Connection,
    findings: list[PublicationAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
        "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
        "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
        "creation_event_id,result_json,error_json FROM board_task_publications "
        "ORDER BY operation_id"
    ).fetchall()
    for raw in rows:
        row = tuple(raw)
        identity = str(row[0])
        request = _stored_request(row)
        if request is None:
            findings.append(PublicationAuditFinding("publication_request_invalid", identity))
            continue
        if row[1] != 1 or not isinstance(row[5], str) or not row[5]:
            findings.append(PublicationAuditFinding("publication_identity_invalid", identity))
        if (
            connection.execute(
                "SELECT 1 FROM identity_agents WHERE agent_id=?",
                (request.actor_id,),
            ).fetchone()
            is None
        ):
            findings.append(PublicationAuditFinding("publication_actor_missing", identity))
        state = row[8]
        if state == "awaiting_lock":
            if any(value is not None for value in row[10:17]):
                findings.append(PublicationAuditFinding("publication_state_invalid", identity))
            if _task_exists(connection, request.task_id):
                findings.append(PublicationAuditFinding("pending_publication_visible", identity))
        elif state == "blocked_integrity":
            if any(value is not None for value in row[10:16]) or not _valid_block_error(row[16]):
                findings.append(PublicationAuditFinding("publication_state_invalid", identity))
            if _task_exists(connection, request.task_id):
                findings.append(PublicationAuditFinding("blocked_publication_visible", identity))
        elif state == "published":
            _audit_published(connection, row, request, findings)
        else:
            findings.append(PublicationAuditFinding("publication_state_invalid", identity))


def _stored_request(row: tuple[object, ...]) -> TaskPublicationRequest | None:
    try:
        decoded: object = json.loads(str(row[3]))
        if not isinstance(decoded, dict):
            return None
        payload = dict(decoded)
        payload["operation_id"] = row[0]
        request = parse_task_publication_request(payload)
    except (ServiceError, TypeError, ValueError):
        return None
    if (
        request.canonical_json != row[3]
        or request.request_sha256 != row[4]
        or request.task_id != row[2]
        or request.actor_id != row[6]
        or request.lock_operation_id != row[7]
    ):
        return None
    return request


def _audit_published(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    request: TaskPublicationRequest,
    findings: list[PublicationAuditFinding],
) -> None:
    identity = request.operation_id
    if (
        not isinstance(row[10], str)
        or not isinstance(row[11], str)
        or type(row[12]) is not int
        or type(row[13]) is not int
        or type(row[14]) is not int
        or not isinstance(row[15], str)
        or row[16] is not None
    ):
        findings.append(PublicationAuditFinding("publication_state_invalid", identity))
        return
    task = connection.execute(
        "SELECT poster_id,title,spec,reward,status,bidding_deadline_seconds,deadline_seconds,"
        "review_deadline_seconds,bidding_deadline,escrow_id,created_at FROM board_tasks "
        "WHERE task_id=?",
        (request.task_id,),
    ).fetchone()
    contract = connection.execute(
        "SELECT lifecycle_version,timing_contract_version,acceptance_window_seconds,"
        "bidding_cutoff_us,acceptance_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (request.task_id,),
    ).fetchone()
    if task is None or contract is None:
        findings.append(PublicationAuditFinding("published_task_missing", identity))
        return
    try:
        published_at_us = int(row[13])
        bidding_cutoff_us = add_seconds_to_epoch_us(
            published_at_us, request.bidding_deadline_seconds
        )
        acceptance_cutoff_us = add_seconds_to_epoch_us(
            bidding_cutoff_us, request.acceptance_window_seconds
        )
        published_at = _timestamp(published_at_us)
        bidding_deadline = _timestamp(bidding_cutoff_us)
        acceptance_deadline = _timestamp(acceptance_cutoff_us)
    except (OverflowError, TypeError, ValueError):
        findings.append(PublicationAuditFinding("publication_clock_invalid", identity))
        return
    if tuple(task[:4]) != (
        request.actor_id,
        request.title,
        request.spec,
        request.reward,
    ) or tuple(task[5:]) != (
        request.bidding_deadline_seconds,
        request.execution_deadline_seconds,
        request.review_deadline_seconds,
        bidding_deadline,
        row[10],
        published_at,
    ):
        findings.append(PublicationAuditFinding("published_task_terms_mismatch", identity))
    if tuple(contract[:5]) != (
        contract[0],
        2,
        request.acceptance_window_seconds,
        bidding_cutoff_us,
        acceptance_cutoff_us,
    ):
        findings.append(PublicationAuditFinding("published_timing_mismatch", identity))
    expected_result = {
        "task_id": request.task_id,
        "poster_id": request.actor_id,
        "title": request.title,
        "spec": request.spec,
        "reward": request.reward,
        "bidding_deadline_seconds": request.bidding_deadline_seconds,
        "acceptance_window_seconds": request.acceptance_window_seconds,
        "execution_deadline_seconds": request.execution_deadline_seconds,
        "review_deadline_seconds": request.review_deadline_seconds,
        "status": "open",
        "escrow_id": row[10],
        "created_at": published_at,
        "bidding_deadline": bidding_deadline,
        "acceptance_deadline": acceptance_deadline,
        "bidding_cutoff_us": bidding_cutoff_us,
        "acceptance_cutoff_us": acceptance_cutoff_us,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": row[14],
    }
    if row[15] != _canonical_json(expected_result):
        findings.append(PublicationAuditFinding("publication_result_mismatch", identity))
    event = connection.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id FROM events WHERE event_id=?",
        (row[14],),
    ).fetchone()
    if event is None or tuple(event) != (
        "task-board",
        "task.created",
        published_at,
        request.task_id,
        request.actor_id,
    ):
        findings.append(PublicationAuditFinding("publication_event_mismatch", identity))
    if not _lock_proof_matches(connection, row, request):
        findings.append(PublicationAuditFinding("publication_lock_proof_mismatch", identity))


def _lock_proof_matches(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    request: TaskPublicationRequest,
) -> bool:
    try:
        proof = inspect_lock_facts(
            connection.cursor(),
            operation_id=request.lock_operation_id,
            payer_account_id=request.actor_id,
            task_id=request.task_id,
            amount=request.reward,
            require_locked=False,
        )
    except PublicationEvidenceError:
        return False
    return bool(
        proof is not None
        and proof.escrow_id == row[10]
        and proof.request_sha256 == row[11]
        and proof.receipt_event_id == row[12]
    )


def _audit_observation_rows(
    connection: sqlite3.Connection,
    cutover: tuple[object, ...] | None,
    findings: list[PublicationAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT observation_id,observation_contract_version,escrow_id,task_id,poster_id,"
        "amount,observed_fact_json,observed_fact_sha256,caller_kid,state,"
        "refund_operation_id,refund_request_json,refund_request_sha256,"
        "refund_receipt_event_id,refunded_at_us,result_json,error_json "
        "FROM board_legacy_posting_observations ORDER BY observation_id"
    ).fetchall()
    for raw in rows:
        row = tuple(raw)
        identity = str(row[0])
        fact = _canonical_object(row[6])
        if (
            row[1] != 1
            or fact is None
            or _sha256(str(row[6])) != row[7]
            or not _observation_fact_shape(fact, row, cutover)
        ):
            findings.append(PublicationAuditFinding("legacy_observation_fact_invalid", identity))
        elif not _observation_provenance_matches(connection, fact, row):
            findings.append(
                PublicationAuditFinding("legacy_observation_provenance_invalid", identity)
            )
        if cutover is None or row[8] != cutover[8]:
            findings.append(PublicationAuditFinding("legacy_observation_owner_mismatch", identity))
        if (
            connection.execute(
                "SELECT 1 FROM board_task_publications WHERE task_id=? LIMIT 1", (row[3],)
            ).fetchone()
            is not None
        ):
            findings.append(PublicationAuditFinding("legacy_task_claimed_natively", identity))
        _audit_observation_recovery(connection, row, fact, findings)


def _observation_fact_shape(
    fact: dict[str, Any],
    row: tuple[object, ...],
    cutover: tuple[object, ...] | None,
) -> bool:
    if set(fact) != {
        "observation_version",
        "source_snapshot_sha256",
        "escrow",
        "lock_provenance",
        "refund_origin",
        "existing_refund_provenance",
    }:
        return False
    escrow = fact.get("escrow")
    if not isinstance(escrow, dict) or set(escrow) != {
        "escrow_id",
        "payer_account_id",
        "amount",
        "task_id",
        "status",
        "created_at",
        "resolved_at",
        "event_id",
    }:
        return False
    lock_provenance = fact["lock_provenance"]
    legacy_lock = isinstance(lock_provenance, dict) and lock_provenance.get("kind") == "legacy"
    if (
        type(fact["observation_version"]) is not int
        or fact["observation_version"] != 1
        or cutover is None
        or not _sha256_text(fact["source_snapshot_sha256"])
        or fact["source_snapshot_sha256"] != cutover[4]
        or not _escrow_fact_shape(escrow, legacy_lock=legacy_lock)
        or (escrow["escrow_id"], escrow["task_id"], escrow["payer_account_id"])
        != (row[2], row[3], row[4])
        or type(escrow["amount"]) is not int
        or type(row[5]) is not int
        or escrow["amount"] != row[5]
        or type(fact["refund_origin"]) is not str
        or fact["refund_origin"] not in {"new_refund", "existing_refund"}
    ):
        return False
    if fact["refund_origin"] == "new_refund":
        return (
            fact["existing_refund_provenance"] is None
            and escrow["status"] == "locked"
            and escrow["resolved_at"] is None
            and _lock_provenance_shape(fact["lock_provenance"])
        )
    return (
        fact["existing_refund_provenance"] is not None
        and escrow["status"] == "released"
        and _timestamp_text(escrow["resolved_at"])
        and _lock_provenance_shape(fact["lock_provenance"])
        and _refund_provenance_shape(fact["existing_refund_provenance"])
    )


def _escrow_fact_shape(escrow: dict[str, Any], *, legacy_lock: bool) -> bool:
    native_event = type(escrow["event_id"]) is int and escrow["event_id"] > 0
    metadata_valid = (
        isinstance(escrow["created_at"], str) and (escrow["event_id"] is None or native_event)
        if legacy_lock
        else _nonempty_text(escrow["created_at"]) and native_event
    )
    return (
        _nonempty_text(escrow["escrow_id"])
        and _nonempty_text(escrow["payer_account_id"])
        and type(escrow["amount"]) is int
        and escrow["amount"] > 0
        and _nonempty_text(escrow["task_id"])
        and type(escrow["status"]) is str
        and escrow["status"] in {"locked", "released", "split"}
        and metadata_valid
        and (
            (escrow["status"] == "locked" and escrow["resolved_at"] is None)
            or (
                escrow["status"] in {"released", "split"} and _timestamp_text(escrow["resolved_at"])
            )
        )
    )


def _lock_provenance_shape(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get("kind")) is not str:
        return False
    if value["kind"] == "native":
        return (
            set(value) == {"kind", "operation_id", "request_sha256", "event_id", "transaction_ids"}
            and _nonempty_text(value["operation_id"])
            and _sha256_text(value["request_sha256"])
            and type(value["event_id"]) is int
            and value["event_id"] > 0
            and _text_list(value["transaction_ids"])
        )
    return (
        value["kind"] == "legacy"
        and set(value)
        == {"kind", "cutover_singleton", "lock_fact_sha256", "transaction_observations"}
        and type(value["cutover_singleton"]) is int
        and value["cutover_singleton"] == 1
        and _sha256_text(value["lock_fact_sha256"])
        and _transaction_observation_list(value["transaction_observations"])
    )


def _refund_provenance_shape(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get("kind")) is not str:
        return False
    if value["kind"] == "native":
        return (
            set(value)
            == {
                "kind",
                "operation_id",
                "request_sha256",
                "event_id",
                "transaction_ids",
                "source_reference",
            }
            and _nonempty_text(value["operation_id"])
            and _sha256_text(value["request_sha256"])
            and type(value["event_id"]) is int
            and value["event_id"] > 0
            and _text_list(value["transaction_ids"])
            and _nonempty_text(value["source_reference"])
        )
    return (
        value["kind"] == "legacy"
        and set(value)
        == {
            "kind",
            "cutover_singleton",
            "resolution_fact_sha256",
            "transaction_observations",
        }
        and type(value["cutover_singleton"]) is int
        and value["cutover_singleton"] == 1
        and _sha256_text(value["resolution_fact_sha256"])
        and _transaction_observation_list(value["transaction_observations"])
    )


def _transaction_observation_list(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    previous: bytes | None = None
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "tx_id",
            "observed_kind",
            "observed_fact_sha256",
        }:
            return False
        if (
            not _nonempty_text(item["tx_id"])
            or not _nonempty_text(item["observed_kind"])
            or not _sha256_text(item["observed_fact_sha256"])
        ):
            return False
        encoded = item["tx_id"].encode("utf-8")
        if previous is not None and encoded <= previous:
            return False
        previous = encoded
    return True


def _text_list(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    if any(not _nonempty_text(item) for item in value):
        return False
    encoded = [item.encode("utf-8") for item in value]
    return encoded == sorted(set(encoded))


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _sha256_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _timestamp_text(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parse_utc_text_to_epoch_us(value)
    except ValueError:
        return False
    return True


def _observation_provenance_matches(
    connection: sqlite3.Connection,
    fact: dict[str, Any],
    row: tuple[object, ...],
) -> bool:
    escrow_fact = fact["escrow"]
    if not isinstance(escrow_fact, dict):
        return False
    escrow = connection.execute(
        "SELECT escrow_id,payer_account_id,amount,task_id,created_at,event_id "
        "FROM bank_escrow WHERE escrow_id=?",
        (row[2],),
    ).fetchone()
    if escrow is None or tuple(escrow) != (
        escrow_fact["escrow_id"],
        escrow_fact["payer_account_id"],
        escrow_fact["amount"],
        escrow_fact["task_id"],
        escrow_fact["created_at"],
        escrow_fact["event_id"],
    ):
        return False
    lock = fact["lock_provenance"]
    if not isinstance(lock, dict) or not _lock_provenance_matches(connection, lock, escrow_fact):
        return False
    existing = fact["existing_refund_provenance"]
    if existing is None:
        return bool(fact["refund_origin"] == "new_refund")
    return bool(
        fact["refund_origin"] == "existing_refund"
        and isinstance(existing, dict)
        and _refund_provenance_matches(connection, existing, escrow_fact)
    )


def _lock_provenance_matches(
    connection: sqlite3.Connection,
    provenance: dict[str, Any],
    escrow: dict[str, Any],
) -> bool:
    if provenance.get("kind") == "native":
        if set(provenance) != {
            "kind",
            "operation_id",
            "request_sha256",
            "event_id",
            "transaction_ids",
        }:
            return False
        expected = canonical_request(
            "escrow_lock",
            {
                "payer_account_id": escrow["payer_account_id"],
                "task_id": escrow["task_id"],
                "amount": escrow["amount"],
            },
        )
        receipt = connection.execute(
            "SELECT operation_kind,request_sha256,event_id,escrow_id,"
            "canonical_request_json FROM bank_operation_receipts WHERE operation_id=?",
            (provenance["operation_id"],),
        ).fetchone()
        return (
            receipt is not None
            and tuple(receipt)
            == (
                "escrow_lock",
                expected.request_sha256,
                provenance["event_id"],
                escrow["escrow_id"],
                expected.canonical_json,
            )
            and provenance["request_sha256"] == expected.request_sha256
            and provenance["transaction_ids"]
            == _receipt_transaction_ids(connection, str(provenance["operation_id"]))
        )
    if provenance.get("kind") != "legacy" or set(provenance) != {
        "kind",
        "cutover_singleton",
        "lock_fact_sha256",
        "transaction_observations",
    }:
        return False
    marker = connection.execute(
        "SELECT cutover_singleton,lock_fact_sha256 FROM bank_legacy_escrow_observations "
        "WHERE escrow_id=?",
        (escrow["escrow_id"],),
    ).fetchone()
    expected_transactions = _legacy_transaction_observations(
        connection,
        transaction_type="escrow_lock",
        reference=str(escrow["task_id"]),
        account_id=str(escrow["payer_account_id"]),
    )
    return (
        marker is not None
        and tuple(marker) == (provenance["cutover_singleton"], provenance["lock_fact_sha256"])
        and provenance["transaction_observations"] == expected_transactions
        and bool(expected_transactions)
    )


def _refund_provenance_matches(
    connection: sqlite3.Connection,
    provenance: dict[str, Any],
    escrow: dict[str, Any],
) -> bool:
    if provenance.get("kind") == "native":
        if set(provenance) != {
            "kind",
            "operation_id",
            "request_sha256",
            "event_id",
            "transaction_ids",
            "source_reference",
        }:
            return False
        receipt = connection.execute(
            "SELECT operation_kind,canonical_request_json,request_sha256,event_id,"
            "escrow_id,source_reference FROM bank_operation_receipts WHERE operation_id=?",
            (provenance["operation_id"],),
        ).fetchone()
        if receipt is None:
            return False
        try:
            request = parse_canonical_request(str(receipt[1]))
            payload = json.loads(request.canonical_json)
        except (TypeError, ValueError):
            return False
        return (
            tuple(receipt[2:])
            == (
                provenance["request_sha256"],
                provenance["event_id"],
                escrow["escrow_id"],
                provenance["source_reference"],
            )
            and receipt[0] == "escrow_release"
            and request.operation_kind == "escrow_release"
            and payload.get("recipient_account_id") == escrow["payer_account_id"]
            and payload.get("escrow_id") == escrow["escrow_id"]
            and provenance["transaction_ids"]
            == _receipt_transaction_ids(connection, str(provenance["operation_id"]))
        )
    if provenance.get("kind") != "legacy" or set(provenance) != {
        "kind",
        "cutover_singleton",
        "resolution_fact_sha256",
        "transaction_observations",
    }:
        return False
    marker = connection.execute(
        "SELECT cutover_singleton,resolution_fact_sha256 "
        "FROM bank_legacy_escrow_observations WHERE escrow_id=?",
        (escrow["escrow_id"],),
    ).fetchone()
    expected_transactions = _legacy_transaction_observations(
        connection,
        transaction_type="escrow_release",
        reference=str(escrow["escrow_id"]),
        account_id=str(escrow["payer_account_id"]),
    )
    return (
        marker is not None
        and tuple(marker) == (provenance["cutover_singleton"], provenance["resolution_fact_sha256"])
        and provenance["transaction_observations"] == expected_transactions
        and bool(expected_transactions)
    )


def _receipt_transaction_ids(
    connection: sqlite3.Connection,
    operation_id: str,
) -> list[str]:
    return sorted(
        (
            str(row[0])
            for row in connection.execute(
                "SELECT transaction_id FROM bank_operation_legs "
                "WHERE operation_id=? AND transaction_id IS NOT NULL",
                (operation_id,),
            )
        ),
        key=lambda value: value.encode("utf-8"),
    )


def _legacy_transaction_observations(
    connection: sqlite3.Connection,
    *,
    transaction_type: str,
    reference: str,
    account_id: str,
) -> list[dict[str, object]]:
    return [
        {
            "tx_id": row[0],
            "observed_kind": row[1],
            "observed_fact_sha256": row[2],
        }
        for row in connection.execute(
            "SELECT o.tx_id,o.observed_kind,o.observed_fact_sha256 "
            "FROM bank_legacy_transaction_observations o "
            "JOIN bank_transactions t ON t.tx_id=o.tx_id "
            "WHERE t.type=? AND t.reference=? AND t.account_id=? ORDER BY o.tx_id",
            (transaction_type, reference, account_id),
        ).fetchall()
    ]


def _audit_observation_recovery(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    fact: dict[str, Any] | None,
    findings: list[PublicationAuditFinding],
) -> None:
    identity = str(row[0])
    if fact is None:
        return
    refund_origin = fact.get("refund_origin")
    original_refund = fact.get("existing_refund_provenance")
    if row[9] == "refund_pending":
        if refund_origin != "new_refund" or original_refund is not None:
            findings.append(PublicationAuditFinding("legacy_refund_origin_mismatch", identity))
        _audit_new_refund_intent(row, findings)
        # A committed receipt may exist before the observation finalization transaction.
        receipt = connection.execute(
            "SELECT canonical_request_json,request_sha256 FROM bank_operation_receipts "
            "WHERE operation_id=?",
            (row[10],),
        ).fetchone()
        if receipt is not None and tuple(receipt) != (row[11], row[12]):
            findings.append(PublicationAuditFinding("legacy_refund_receipt_conflict", identity))
    elif row[9] == "refunded":
        if refund_origin == "existing_refund":
            _audit_existing_refund_completion(
                connection,
                row,
                fact,
                findings,
            )
        elif refund_origin == "new_refund" and original_refund is None:
            _audit_new_refund_intent(row, findings)
            _audit_native_refund_completion(connection, row, findings)
        else:
            findings.append(PublicationAuditFinding("legacy_refund_origin_mismatch", identity))
    elif row[9] == "blocked_integrity":
        if refund_origin != "new_refund" or original_refund is not None:
            findings.append(PublicationAuditFinding("legacy_refund_origin_mismatch", identity))
        if not _valid_legacy_block_error(row[16]):
            findings.append(PublicationAuditFinding("legacy_block_error_invalid", identity))
        _audit_new_refund_intent(row, findings)
    else:
        findings.append(PublicationAuditFinding("legacy_observation_state_invalid", identity))


def _audit_new_refund_intent(
    row: tuple[object, ...],
    findings: list[PublicationAuditFinding],
) -> None:
    expected_id = operation_id_for("task-posting-refund", (str(row[2]),))
    expected = canonical_request(
        "escrow_release",
        {
            "escrow_id": row[2],
            "recipient_account_id": row[4],
            "source_reference": f"task:{row[3]}:posting_refund:{row[2]}",
        },
    )
    if row[10:13] != (expected_id, expected.canonical_json, expected.request_sha256):
        findings.append(PublicationAuditFinding("legacy_refund_intent_invalid", str(row[0])))


def _audit_existing_refund_completion(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    fact: dict[str, Any],
    findings: list[PublicationAuditFinding],
) -> None:
    identity = str(row[0])
    original = fact.get("existing_refund_provenance")
    escrow_fact = fact.get("escrow")
    if not isinstance(original, dict) or not isinstance(escrow_fact, dict):
        findings.append(PublicationAuditFinding("legacy_refund_origin_mismatch", identity))
        return
    if original.get("kind") == "legacy":
        expected_result = _canonical_json(
            {
                "refund_origin": "existing_refund",
                "settlement_provenance_kind": "legacy_observation",
                "escrow_status_at_cutover": "released",
            }
        )
        if (
            any(value is not None for value in row[10:14])
            or row[15] != expected_result
            or not _timestamp_us_matches(row[14], escrow_fact.get("resolved_at"))
        ):
            findings.append(PublicationAuditFinding("legacy_refund_result_invalid", identity))
        return
    if original.get("kind") != "native" or row[10] != original.get("operation_id"):
        findings.append(PublicationAuditFinding("legacy_refund_origin_mismatch", identity))
        return
    _audit_native_refund_completion(connection, row, findings)
    receipt = connection.execute(
        "SELECT committed_at FROM bank_operation_receipts WHERE operation_id=?",
        (original["operation_id"],),
    ).fetchone()
    if receipt is None or escrow_fact.get("resolved_at") != receipt[0]:
        findings.append(PublicationAuditFinding("legacy_refund_result_invalid", identity))


def _audit_native_refund_completion(
    connection: sqlite3.Connection,
    row: tuple[object, ...],
    findings: list[PublicationAuditFinding],
) -> None:
    identity = str(row[0])
    receipt = connection.execute(
        "SELECT canonical_request_json,request_sha256,event_id,result_json,committed_at "
        "FROM bank_operation_receipts WHERE operation_id=?",
        (row[10],),
    ).fetchone()
    escrow = connection.execute(
        "SELECT status,resolved_at FROM bank_escrow WHERE escrow_id=?",
        (row[2],),
    ).fetchone()
    if (
        receipt is None
        or tuple(receipt[:4]) != (row[11], row[12], row[13], row[15])
        or escrow is None
        or tuple(escrow) != ("released", receipt[4])
        or not _timestamp_us_matches(row[14], receipt[4])
    ):
        findings.append(PublicationAuditFinding("legacy_refund_result_invalid", identity))


def _audit_attested_sets(
    connection: sqlite3.Connection,
    cutover: tuple[object, ...] | None,
    findings: list[PublicationAuditFinding],
) -> None:
    if cutover is None:
        return
    legacy_tasks = sorted(
        (
            str(row[0])
            for row in connection.execute(
                "SELECT t.task_id FROM board_tasks t LEFT JOIN board_task_publications p "
                "ON p.task_id=t.task_id AND p.state='published' "
                "WHERE p.operation_id IS NULL"
            )
        ),
        key=lambda value: value.encode("utf-8"),
    )
    if (cutover[9], cutover[10]) != (_sha256(_canonical_json(legacy_tasks)), len(legacy_tasks)):
        findings.append(PublicationAuditFinding("legacy_task_attestation_mismatch", "1"))
    observations = [
        [row[0], row[1], row[2]]
        for row in connection.execute(
            "SELECT observation_id,escrow_id,observed_fact_sha256 "
            "FROM board_legacy_posting_observations ORDER BY observation_id COLLATE BINARY"
        ).fetchall()
    ]
    if (cutover[11], cutover[12]) != (
        _sha256(_canonical_json(observations)),
        len(observations),
    ):
        findings.append(PublicationAuditFinding("legacy_observation_attestation_mismatch", "1"))


def _valid_block_error(raw: object) -> bool:
    value = _canonical_object(raw)
    details = None if value is None else value.get("details")
    return bool(
        value is not None
        and set(value) == {"error", "message", "details"}
        and value["error"] == "publication_integrity_blocked"
        and value["message"] == "Publication evidence requires reconciliation"
        and isinstance(details, dict)
        and set(details) == {"reason"}
        and details["reason"] in _BLOCK_REASONS
    )


def _valid_legacy_block_error(raw: object) -> bool:
    value = _canonical_object(raw)
    if value is None or set(value) != {"error", "message", "details"}:
        return False
    details = value["details"]
    return (
        value["error"] == "posting_refund_integrity_blocked"
        and value["message"] == "Posting refund evidence requires reconciliation"
        and isinstance(details, dict)
        and set(details) == {"reason"}
        and type(details["reason"]) is str
        and details["reason"] in _LEGACY_BLOCK_REASONS
    )


def _timestamp_us_matches(stored: object, timestamp: object) -> bool:
    if type(stored) is not int or not isinstance(timestamp, str):
        return False
    try:
        rendered = _timestamp(stored)
    except (OverflowError, TypeError, ValueError):
        return False
    return rendered == timestamp


def _canonical_object(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value: object = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) and _canonical_json(value) == raw else None


def _task_exists(connection: sqlite3.Connection, task_id: str) -> bool:
    return (
        connection.execute("SELECT 1 FROM board_tasks WHERE task_id=?", (task_id,)).fetchone()
        is not None
    )


def _timestamp(value: int) -> str:
    rendered = epoch_us_to_utc_text(value)
    head, separator, fraction = rendered.partition(".")
    if separator == "":
        return rendered[:-1] + ".000000Z"
    return head + "." + fraction[:-1].ljust(6, "0") + "Z"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result(
    connection: sqlite3.Connection,
    findings: list[PublicationAuditFinding],
    tables: set[str],
) -> PublicationAuditResult:
    publication_count = (
        int(connection.execute("SELECT COUNT(*) FROM board_task_publications").fetchone()[0])
        if "board_task_publications" in tables
        else 0
    )
    legacy_count = (
        int(
            connection.execute("SELECT COUNT(*) FROM board_legacy_posting_observations").fetchone()[
                0
            ]
        )
        if "board_legacy_posting_observations" in tables
        else 0
    )
    ordered = tuple(sorted(set(findings)))
    return PublicationAuditResult(not ordered, ordered, publication_count, legacy_count)


__all__ = [
    "PublicationAuditFinding",
    "PublicationAuditResult",
    "audit_publications",
]
