"""Read-only audit for MP-07 task-contract and legacy-observation ownership."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import add_seconds_to_epoch_us, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3

_REQUIRED_TABLES = frozenset(
    {"board_task_contracts", "board_task_operations", "board_legacy_effect_observations"}
)
_TASK_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "expired", "disputed", "ruled"}
)
_ACCEPTED_TERMS_REQUIRED = frozenset({"accepted", "submitted", "approved", "disputed", "ruled"})
_ACCEPTED_TERMS_FORBIDDEN = frozenset({"open", "cancelled"})
_SUBMITTED_PHASE_STATUSES = frozenset({"submitted", "approved", "disputed", "ruled"})
_DISPUTE_STATUSES = frozenset({"disputed", "ruled"})
_BANK_EVENT_SOURCE = "central-bank"
_FORBIDDEN_FACT_KEYS = frozenset(
    {
        "operation_id",
        "client_operation_id",
        "caller_kid",
        "actor_kind",
        "actor_id",
        "command",
        "command_sha256",
        "request_sha256",
    }
)
_SHARED_FACT_KEYS = frozenset(
    {
        "task_id",
        "task_status",
        "poster_id",
        "worker_id",
        "escrow_id",
        "reward",
        "accepted_bid_id",
        "accepted_bid_amount",
        "external_reference",
    }
)
_FACT_KEYS = {
    "worker_settlement_succeeded": _SHARED_FACT_KEYS
    | {
        "escrow_status",
        "resolved_at",
        "recipient_id",
        "settled_amount",
        "settlement_source_reference",
        "external_receipt_request_sha256",
        "external_receipt_event_id",
        "external_receipt_transaction_id",
    },
    "worker_settlement_pending": _SHARED_FACT_KEYS | {"escrow_status", "escrow_pending"},
    "court_claim_succeeded": _SHARED_FACT_KEYS
    | {
        "claimant_id",
        "respondent_id",
        "reason",
        "claim_status",
        "filed_at",
        "rebuttal_deadline",
    },
}


@dataclass(frozen=True, order=True, slots=True)
class TaskContractAuditFinding:
    """Stable machine-readable task-contract finding."""

    code: str
    task_id: str


@dataclass(frozen=True, slots=True)
class TaskContractAuditResult:
    """Task-contract audit status; it does not authorize startup by itself."""

    contracts_clean: bool
    findings: tuple[TaskContractAuditFinding, ...]
    task_count: int
    contract_count: int
    operation_count: int
    legacy_observation_count: int


def audit_task_contracts(connection: sqlite3.Connection) -> TaskContractAuditResult:
    """Verify total sidecar coverage, immutable term linkage and observation facts."""
    findings: list[TaskContractAuditFinding] = []
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        findings.extend(
            TaskContractAuditFinding("missing_task_contract_table", name) for name in missing
        )
        return _result(connection, findings, tables)

    task_ids = {
        str(row[0]) for row in connection.execute("SELECT task_id FROM board_tasks").fetchall()
    }
    contract_ids = {
        str(row[0])
        for row in connection.execute("SELECT task_id FROM board_task_contracts").fetchall()
    }
    findings.extend(
        TaskContractAuditFinding("missing_task_contract", task_id)
        for task_id in sorted(task_ids - contract_ids)
    )
    findings.extend(
        TaskContractAuditFinding("orphan_task_contract", task_id)
        for task_id in sorted(contract_ids - task_ids)
    )
    for task_id in sorted(task_ids & contract_ids):
        _audit_task(connection, task_id, findings)
    _audit_observations(connection, findings)
    return _result(connection, findings, tables)


def _audit_task(
    connection: sqlite3.Connection,
    task_id: str,
    findings: list[TaskContractAuditFinding],
) -> None:
    row = connection.execute(
        "SELECT t.reward, t.worker_id, t.accepted_bid_id, t.bidding_deadline, "
        "t.execution_deadline, t.review_deadline, c.timing_contract_version, "
        "c.acceptance_window_seconds, c.bidding_cutoff_us, c.acceptance_cutoff_us, "
        "c.execution_cutoff_us, c.review_cutoff_us, c.accepted_reward, "
        "c.accepted_bid_amount, t.status, t.created_at, t.bidding_deadline_seconds, "
        "t.accepted_at, t.deadline_seconds, t.submitted_at, t.review_deadline_seconds "
        "FROM board_tasks t JOIN board_task_contracts c "
        "ON c.task_id=t.task_id WHERE t.task_id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        findings.append(TaskContractAuditFinding("task_contract_unreadable", task_id))
        return
    status = str(row[14])
    if status not in _TASK_STATUSES:
        findings.append(TaskContractAuditFinding("task_state_inconsistent", task_id))
    if not _cutoffs_are_consistent(row):
        findings.append(TaskContractAuditFinding("task_cutoff_inconsistent", task_id))

    worker_id, accepted_bid_id = row[1], row[2]
    accepted_reward, accepted_bid_amount = row[12], row[13]
    has_accepted_terms = all(
        value is not None
        for value in (worker_id, accepted_bid_id, accepted_reward, accepted_bid_amount)
    )
    if worker_id is None and accepted_bid_id is None:
        if (
            status in _ACCEPTED_TERMS_REQUIRED
            or accepted_reward is not None
            or accepted_bid_amount is not None
        ):
            findings.append(TaskContractAuditFinding("accepted_bid_linkage_invalid", task_id))
    elif worker_id is None or accepted_bid_id is None:
        findings.append(TaskContractAuditFinding("accepted_bid_linkage_invalid", task_id))
    else:
        bid = connection.execute(
            "SELECT bidder_id, amount FROM board_bids WHERE bid_id=? AND task_id=?",
            (accepted_bid_id, task_id),
        ).fetchone()
        if (
            bid is None
            or tuple(bid) != (worker_id, accepted_bid_amount)
            or accepted_reward != row[0]
        ):
            findings.append(TaskContractAuditFinding("accepted_bid_linkage_invalid", task_id))
    if status in _ACCEPTED_TERMS_FORBIDDEN and has_accepted_terms:
        findings.append(TaskContractAuditFinding("accepted_bid_linkage_invalid", task_id))

    if not _dispute_state_is_consistent(connection, task_id, status):
        findings.append(TaskContractAuditFinding("dispute_claim_linkage_invalid", task_id))


def _dispute_state_is_consistent(
    connection: sqlite3.Connection,
    task_id: str,
    status: str,
) -> bool:
    task = connection.execute(
        "SELECT poster_id, worker_id, dispute_reason, dispute_id, disputed_at, ruling_id, "
        "worker_pct, ruling_summary, ruled_at FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if task is None:
        return False
    dispute_values = tuple(task[2:5])
    ruling_values = tuple(task[5:9])
    if status not in _DISPUTE_STATUSES:
        return all(value is None for value in (*dispute_values, *ruling_values))
    if any(value is None for value in dispute_values):
        return False
    claim = connection.execute(
        "SELECT task_id, claimant_id, respondent_id, reason FROM court_claims WHERE claim_id=?",
        (task[3],),
    ).fetchone()
    if claim is None or tuple(claim) != (task_id, task[0], task[1], task[2]):
        return False
    if status == "disputed":
        return all(value is None for value in ruling_values)
    return _ruling_state_matches(connection, task_id, task, ruling_values)


def _ruling_state_matches(
    connection: sqlite3.Connection,
    task_id: str,
    task: sqlite3.Row | tuple[object, ...],
    ruling_values: tuple[object, ...],
) -> bool:
    if any(value is None for value in ruling_values) or task[5] != task[3]:
        return False
    rulings = connection.execute(
        "SELECT ruling_id, claim_id, task_id, worker_pct, summary FROM court_rulings "
        "WHERE ruling_id=? OR task_id=? ORDER BY ruling_id",
        (task[5], task_id),
    ).fetchall()
    if rulings:
        return len(rulings) == 1 and tuple(rulings[0]) == (
            task[5],
            task[3],
            task_id,
            task[6],
            task[7],
        )
    return _native_ruling_completion_matches(connection, task_id, task)


def _native_ruling_completion_matches(
    connection: sqlite3.Connection,
    task_id: str,
    task: sqlite3.Row | tuple[object, ...],
) -> bool:
    operations = connection.execute(
        "SELECT operation_id, canonical_request_json, request_sha256, source_status, "
        "source_lifecycle_version, target_status, target_lifecycle_version, "
        "operation_state, effect_kind, effect_state, effect_operation_id, "
        "effect_result_reference, effect_intent_json, event_id, result_json "
        "FROM board_task_operations WHERE task_id=? AND command='record_ruling' "
        "ORDER BY operation_id",
        (task_id,),
    ).fetchall()
    if len(operations) != 1:
        return False
    operation = tuple(operations[0])
    request = _stored_ruling_request(operation)
    if request is None:
        return False
    context = connection.execute(
        "SELECT t.poster_id, t.worker_id, t.escrow_id, t.reward, "
        "c.lifecycle_version, c.accepted_reward FROM board_tasks t "
        "JOIN board_task_contracts c ON c.task_id=t.task_id WHERE t.task_id=?",
        (task_id,),
    ).fetchone()
    if context is None:
        return False
    worker_pct = task[6]
    if type(worker_pct) is not int:
        return False
    accepted_reward = context[5]
    reward = context[3] if accepted_reward is None else accepted_reward
    expected_arguments = {
        "poster_id": context[0],
        "worker_id": context[1],
        "escrow_id": context[2],
        "reward": reward,
        "ruling_id": task[3],
        "worker_pct": worker_pct,
        "summary": task[7],
        "source_reference": f"task:{task_id}:ruling:{task[3]}",
    }
    expected_effect_kind = "bank_split" if worker_pct not in {0, 100} else "bank_release"
    expected_result = {
        "task_id": task_id,
        "command": "record_ruling",
        "status": "ruled",
        "lifecycle_version": context[4],
        "event_id": operation[13],
        "effect_result_reference": operation[0],
        "worker_id": context[1],
        "ruling_id": task[3],
        "worker_pct": worker_pct,
        "summary": task[7],
        "reward": reward,
    }
    return (
        request.task_id == task_id
        and request.expected_lifecycle_version == context[4] - 1
        and dict(request.arguments) == expected_arguments
        and operation[3:12]
        == (
            "disputed",
            context[4] - 1,
            "ruled",
            context[4],
            "completed",
            expected_effect_kind,
            "succeeded",
            operation[0],
            operation[0],
        )
        and _json_object(operation[12])
        == _expected_ruling_effect_intent(
            task_id,
            context,
            claim_id=str(task[3]),
            worker_pct=worker_pct,
            reward=int(reward),
        )
        and _json_object(operation[14]) == expected_result
        and _board_ruling_event_matches(
            connection, operation[13], task_id, operation_id=str(operation[0])
        )
        and _ruling_receipt_matches(
            connection,
            str(operation[0]),
            task_id,
            context,
            claim_id=str(task[3]),
            worker_pct=worker_pct,
        )
    )


def _stored_ruling_request(
    operation: tuple[object, ...],
) -> TaskOperationRequest | None:
    try:
        decoded: object = json.loads(str(operation[1]))
        if not isinstance(decoded, dict):
            return None
        payload = dict(decoded)
        payload["operation_id"] = operation[0]
        request = parse_task_operation_request(payload)
    except (TypeError, ValueError, ServiceError):
        return None
    if (
        request.command != "record_ruling"
        or request.canonical_json != operation[1]
        or request.request_sha256 != operation[2]
    ):
        return None
    return request


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded: object = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        return None
    return {str(key): item for key, item in decoded.items()}


def _expected_ruling_effect_intent(
    task_id: str,
    context: sqlite3.Row | tuple[object, ...],
    *,
    claim_id: str,
    worker_pct: int,
    reward: int,
) -> dict[str, object]:
    source_reference = f"task:{task_id}:ruling:{claim_id}"
    if worker_pct in {0, 100}:
        kind = "escrow_release"
        fields = {
            "escrow_id": context[2],
            "recipient_account_id": context[0] if worker_pct == 0 else context[1],
            "source_reference": source_reference,
        }
    else:
        kind = "escrow_split"
        fields = {
            "escrow_id": context[2],
            "worker_account_id": context[1],
            "poster_account_id": context[0],
            "worker_pct": worker_pct,
            "source_reference": source_reference,
        }
    request = canonical_request(kind, fields)
    return {
        "financial_request": json.loads(request.canonical_json),
        "financial_request_sha256": request.request_sha256,
        "expected_escrow": {
            "escrow_id": context[2],
            "task_id": task_id,
            "payer_account_id": context[0],
            "amount": reward,
        },
    }


def _board_ruling_event_matches(
    connection: sqlite3.Connection,
    event_id: object,
    task_id: str,
    *,
    operation_id: str,
) -> bool:
    event = connection.execute(
        "SELECT event_source, event_type, task_id, agent_id, payload FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return event is not None and tuple(event) == (
        "board",
        "task.ruled",
        task_id,
        None,
        json.dumps(
            {"command": "record_ruling", "operation_id": operation_id},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _ruling_receipt_matches(
    connection: sqlite3.Connection,
    operation_id: str,
    task_id: str,
    context: sqlite3.Row | tuple[object, ...],
    *,
    claim_id: str,
    worker_pct: int,
) -> bool:
    reward = context[3] if context[5] is None else context[5]
    if type(reward) is not int:
        return False
    expected_intent = _expected_ruling_effect_intent(
        task_id,
        context,
        claim_id=claim_id,
        worker_pct=worker_pct,
        reward=reward,
    )
    financial_request = expected_intent["financial_request"]
    assert isinstance(financial_request, dict)
    expected_kind = "escrow_release" if worker_pct in {0, 100} else "escrow_split"
    receipt = connection.execute(
        "SELECT receipt_version, operation_kind, canonical_request_json, request_sha256, "
        "source_reference, escrow_id, event_id FROM bank_operation_receipts "
        "WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if receipt is None:
        return False
    event_type = "escrow.released" if expected_kind == "escrow_release" else "escrow.split"
    event = connection.execute(
        "SELECT event_source, event_type, task_id FROM events WHERE event_id=?",
        (receipt[6],),
    ).fetchone()
    expected_escrow_status = "released" if expected_kind == "escrow_release" else "split"
    escrow = connection.execute(
        "SELECT payer_account_id, amount, task_id, status, resolved_at "
        "FROM bank_escrow WHERE escrow_id=?",
        (context[2],),
    ).fetchone()
    return (
        tuple(receipt)
        == (
            1,
            expected_kind,
            json.dumps(financial_request, sort_keys=True, separators=(",", ":")),
            expected_intent["financial_request_sha256"],
            financial_request["source_reference"],
            context[2],
            receipt[6],
        )
        and event is not None
        and str(event[0]) == _BANK_EVENT_SOURCE
        and str(event[1]) == event_type
        and (event[2] is None or event[2] == task_id)
        and escrow is not None
        and tuple(escrow[0:4])
        == (
            context[0],
            context[3] if context[5] is None else context[5],
            task_id,
            expected_escrow_status,
        )
        and escrow[4] is not None
    )


def _cutoffs_are_consistent(row: sqlite3.Row | tuple[object, ...]) -> bool:
    try:
        _validate_cutoffs(row)
    except ValueError:
        return False
    return True


def _validate_cutoffs(row: sqlite3.Row | tuple[object, ...]) -> None:
    bidding_us = parse_utc_text_to_epoch_us(row[3])
    created_us = parse_utc_text_to_epoch_us(row[15])
    bidding_seconds = _positive_duration(row[16])
    if bidding_us != add_seconds_to_epoch_us(created_us, bidding_seconds):
        raise ValueError("bidding cutoff is inconsistent")
    execution_us = None if row[4] is None else parse_utc_text_to_epoch_us(row[4])
    accepted_us = None if row[17] is None else parse_utc_text_to_epoch_us(row[17])
    deadline_seconds = _positive_duration(row[18])
    review_us = None if row[5] is None else parse_utc_text_to_epoch_us(row[5])
    submitted_us = None if row[19] is None else parse_utc_text_to_epoch_us(row[19])
    review_seconds = _positive_duration(row[20])
    accepted_phase_present = accepted_us is not None and execution_us is not None
    submitted_phase_present = submitted_us is not None and review_us is not None
    accepted_terms_present = all(value is not None for value in (row[1], row[2], row[12], row[13]))
    submitted_status = row[14] in _SUBMITTED_PHASE_STATUSES
    if (accepted_us is None) != (execution_us is None) or (
        accepted_terms_present != accepted_phase_present
    ):
        raise ValueError("accepted phase is inconsistent")
    if (submitted_us is None) != (review_us is None) or (
        submitted_status != submitted_phase_present
    ):
        raise ValueError("submitted phase is inconsistent")
    if accepted_us is not None and execution_us != add_seconds_to_epoch_us(
        accepted_us, deadline_seconds
    ):
        raise ValueError("execution cutoff is inconsistent")
    if submitted_us is not None and review_us != add_seconds_to_epoch_us(
        submitted_us, review_seconds
    ):
        raise ValueError("review cutoff is inconsistent")
    if row[8] != bidding_us or row[10] != execution_us or row[11] != review_us:
        raise ValueError("stored cutoff is inconsistent")
    if row[6] == 1 and (row[7] is not None or row[9] != bidding_us):
        raise ValueError("legacy acceptance cutoff is inconsistent")


def _positive_duration(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("duration must be a positive integer")
    return value


def _audit_observations(
    connection: sqlite3.Connection,
    findings: list[TaskContractAuditFinding],
) -> None:
    rows = connection.execute(
        "SELECT observation_id, task_id, observation_kind, observed_fact_json, "
        "observed_fact_sha256, external_reference, unresolved "
        "FROM board_legacy_effect_observations "
        "ORDER BY observation_id"
    ).fetchall()
    for row in rows:
        task_id = str(row[1])
        try:
            observed = json.loads(str(row[3]))
        except (TypeError, ValueError):
            findings.append(TaskContractAuditFinding("legacy_observation_invalid", task_id))
            continue
        if not isinstance(observed, dict) or not all(isinstance(key, str) for key in observed):
            findings.append(TaskContractAuditFinding("legacy_observation_invalid", task_id))
            continue
        observed_keys = {str(key) for key in observed}
        canonical = json.dumps(
            observed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        valid = (
            observed_keys == set(_FACT_KEYS.get(str(row[2]), frozenset()))
            and all(not isinstance(value, (dict, list, tuple)) for value in observed.values())
            and _no_forbidden_fact_keys(observed)
            and canonical == row[3]
            and hashlib.sha256(canonical.encode("utf-8")).hexdigest() == row[4]
            and observed.get("task_id") == task_id
            and observed.get("external_reference") == row[5]
            and _observation_matches_source(
                connection,
                str(row[2]),
                observed,
                require_cutover_projection=row[6] == 1,
            )
        )
        if not valid:
            findings.append(TaskContractAuditFinding("legacy_observation_invalid", task_id))


def _no_forbidden_fact_keys(value: object) -> bool:
    if isinstance(value, dict):
        if not _FORBIDDEN_FACT_KEYS.isdisjoint(str(key) for key in value):
            return False
        return all(_no_forbidden_fact_keys(nested) for nested in value.values())
    if isinstance(value, (list, tuple)):
        return all(_no_forbidden_fact_keys(nested) for nested in value)
    return True


def _observation_matches_source(
    connection: sqlite3.Connection,
    kind: str,
    observed: dict[object, object],
    *,
    require_cutover_projection: bool,
) -> bool:
    task_id = observed.get("task_id")
    task = connection.execute(
        "SELECT status, poster_id, worker_id, escrow_id, reward, accepted_bid_id, "
        "escrow_pending FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if task is None:
        return False
    bid = connection.execute(
        "SELECT amount FROM board_bids WHERE bid_id=? AND task_id=?",
        (task[5], task_id),
    ).fetchone()
    common = {
        "task_id": task_id,
        "poster_id": task[1],
        "worker_id": task[2],
        "escrow_id": task[3],
        "reward": task[4],
        "accepted_bid_id": task[5],
        "accepted_bid_amount": None if bid is None else bid[0],
        "external_reference": observed.get("external_reference"),
    }
    if any(observed.get(key) != value for key, value in common.items()):
        return False
    if not require_cutover_projection:
        return _resolved_observation_matches(connection, kind, observed, task, task_id)
    if observed.get("task_status") != task[0]:
        return False
    return _cutover_observation_matches(connection, kind, observed, task, task_id)


def _cutover_observation_matches(
    connection: sqlite3.Connection,
    kind: str,
    observed: dict[object, object],
    task: sqlite3.Row | tuple[object, ...],
    task_id: object,
) -> bool:
    if kind == "worker_settlement_pending":
        matches = _pending_observation_matches(
            connection,
            observed,
            task,
            task_id,
        )
    elif kind == "worker_settlement_succeeded":
        matches = _settlement_observation_matches(connection, observed, task)
    elif kind == "court_claim_succeeded":
        matches = _court_observation_matches(connection, observed, task_id)
    else:
        matches = False
    return matches


def _pending_observation_matches(
    connection: sqlite3.Connection,
    observed: dict[object, object],
    task: sqlite3.Row | tuple[object, ...],
    task_id: object,
) -> bool:
    escrow = connection.execute(
        "SELECT status FROM bank_escrow WHERE escrow_id=? AND task_id=?",
        (task[3], task_id),
    ).fetchone()
    return (
        escrow is not None
        and observed.get("escrow_status") == escrow[0]
        and observed.get("escrow_pending") == task[6]
    )


def _resolved_observation_matches(
    connection: sqlite3.Connection,
    kind: str,
    observed: dict[object, object],
    task: sqlite3.Row | tuple[object, ...],
    task_id: object,
) -> bool:
    if kind == "worker_settlement_pending":
        return _worker_settlement_outcome_matches(connection, observed, task, task_id)
    if kind == "worker_settlement_succeeded":
        return _settlement_observation_matches(
            connection, observed, task
        ) and _worker_settlement_outcome_matches(connection, observed, task, task_id)
    if kind == "court_claim_succeeded":
        return _resolved_court_observation_matches(connection, observed, task_id)
    return False


def _worker_settlement_outcome_matches(
    connection: sqlite3.Connection,
    observed: dict[object, object],
    task: sqlite3.Row | tuple[object, ...],
    task_id: object,
) -> bool:
    if task[0] != "approved" or task[6] != 0:
        return False
    operation_id = observed.get("external_reference")
    source_reference = f"task:{task_id}:approved"
    expected = canonical_request(
        "escrow_release",
        {
            "escrow_id": task[3],
            "recipient_account_id": task[2],
            "source_reference": source_reference,
        },
    )
    receipt = connection.execute(
        "SELECT r.receipt_version, r.operation_kind, r.canonical_request_json, "
        "r.request_sha256, r.source_reference, r.event_id, e.status, e.resolved_at "
        "FROM bank_operation_receipts r JOIN bank_escrow e ON e.escrow_id=r.escrow_id "
        "WHERE r.operation_id=? AND r.escrow_id=? AND e.task_id=?",
        (operation_id, task[3], task_id),
    ).fetchone()
    if (
        receipt is None
        or tuple(receipt[0:7])
        != (
            1,
            "escrow_release",
            expected.canonical_json,
            expected.request_sha256,
            source_reference,
            receipt[5],
            "released",
        )
        or receipt[7] is None
    ):
        return False
    event = connection.execute(
        "SELECT event_source, event_type, task_id, agent_id FROM events WHERE event_id=?",
        (receipt[5],),
    ).fetchone()
    leg = connection.execute(
        "SELECT l.account_id, l.direction, l.amount, l.transaction_id, l.balance_after, "
        "t.account_id, t.type, t.amount, t.balance_after, t.reference, t.event_id "
        "FROM bank_operation_legs l JOIN bank_transactions t ON t.tx_id=l.transaction_id "
        "WHERE l.operation_id=? AND l.role='recipient'",
        (operation_id,),
    ).fetchone()
    return (
        event is not None
        and str(event[0]) == _BANK_EVENT_SOURCE
        and str(event[1]) == "escrow.released"
        and (event[2] is None or event[2] == task_id)
        and event[3] == task[2]
        and leg is not None
        and tuple(leg)
        == (
            task[2],
            "credit",
            task[4],
            leg[3],
            leg[4],
            task[2],
            "escrow_release",
            task[4],
            leg[4],
            task[3],
            receipt[5],
        )
        and leg[3] is not None
        and leg[4] is not None
    )


def _resolved_court_observation_matches(
    connection: sqlite3.Connection,
    observed: dict[object, object],
    task_id: object,
) -> bool:
    task = connection.execute(
        "SELECT status, poster_id, worker_id, dispute_reason, dispute_id "
        "FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if (
        task is None
        or task[0] not in _DISPUTE_STATUSES
        or task[3] != observed.get("reason")
        or task[4] != observed.get("external_reference")
    ):
        return False
    claim = connection.execute(
        "SELECT claimant_id, respondent_id, reason, filed_at, rebuttal_deadline "
        "FROM court_claims WHERE claim_id=? AND task_id=?",
        (observed.get("external_reference"), task_id),
    ).fetchone()
    return claim is not None and tuple(claim) == (
        task[1],
        task[2],
        observed.get("reason"),
        observed.get("filed_at"),
        observed.get("rebuttal_deadline"),
    )


def _settlement_observation_matches(
    connection: sqlite3.Connection,
    observed: dict[object, object],
    task: sqlite3.Row | tuple[object, ...],
) -> bool:
    receipt = connection.execute(
        "SELECT r.request_sha256, r.source_reference, r.event_id, e.status, e.resolved_at "
        "FROM bank_operation_receipts r JOIN bank_escrow e ON e.escrow_id=r.escrow_id "
        "WHERE r.operation_id=? AND r.escrow_id=?",
        (observed.get("external_reference"), task[3]),
    ).fetchone()
    leg = connection.execute(
        "SELECT account_id, amount, transaction_id FROM bank_operation_legs "
        "WHERE operation_id=? AND role='recipient'",
        (observed.get("external_reference"),),
    ).fetchone()
    return (
        receipt is not None
        and leg is not None
        and {
            "external_receipt_request_sha256": receipt[0],
            "settlement_source_reference": receipt[1],
            "external_receipt_event_id": receipt[2],
            "escrow_status": receipt[3],
            "resolved_at": receipt[4],
            "recipient_id": leg[0],
            "settled_amount": leg[1],
            "external_receipt_transaction_id": leg[2],
        }
        == {
            key: observed.get(key)
            for key in (
                "external_receipt_request_sha256",
                "settlement_source_reference",
                "external_receipt_event_id",
                "escrow_status",
                "resolved_at",
                "recipient_id",
                "settled_amount",
                "external_receipt_transaction_id",
            )
        }
    )


def _court_observation_matches(
    connection: sqlite3.Connection,
    observed: dict[object, object],
    task_id: object,
) -> bool:
    claim = connection.execute(
        "SELECT claimant_id, respondent_id, reason, status, filed_at, rebuttal_deadline "
        "FROM court_claims WHERE claim_id=? AND task_id=?",
        (observed.get("external_reference"), task_id),
    ).fetchone()
    return claim is not None and tuple(claim) == tuple(
        observed.get(key)
        for key in (
            "claimant_id",
            "respondent_id",
            "reason",
            "claim_status",
            "filed_at",
            "rebuttal_deadline",
        )
    )


def _result(
    connection: sqlite3.Connection,
    findings: list[TaskContractAuditFinding],
    tables: set[str],
) -> TaskContractAuditResult:
    def count(table: str) -> int:
        if table not in tables:
            return 0
        row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()  # nosec B608
        return 0 if row is None else int(row[0])

    stable = tuple(sorted(set(findings)))
    return TaskContractAuditResult(
        contracts_clean=not stable,
        findings=stable,
        task_count=count("board_tasks"),
        contract_count=count("board_task_contracts"),
        operation_count=count("board_task_operations"),
        legacy_observation_count=count("board_legacy_effect_observations"),
    )


__all__ = [
    "TaskContractAuditFinding",
    "TaskContractAuditResult",
    "audit_task_contracts",
]
