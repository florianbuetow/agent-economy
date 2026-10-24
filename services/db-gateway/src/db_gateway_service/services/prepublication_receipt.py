"""V2 receipt adoption, time feasibility, and public graph materialization."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.task_publication_financial import (
    PublicationEvidenceError,
    PublicationFinancialProof,
    inspect_lock_facts,
)
from db_gateway_service.task_time import (
    add_seconds_to_epoch_us,
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from db_gateway_service.task_publication_v2_requests import PublicationAdmissionV2

MAX_UTC_US = parse_utc_text_to_epoch_us("9999-12-31T23:59:59.999999Z")


def fixed_utc(value: int) -> str:
    rendered = epoch_us_to_utc_text(value).removesuffix("Z")
    if "." not in rendered:
        return f"{rendered}.000000Z"
    whole, fraction = rendered.split(".", 1)
    return f"{whole}.{fraction.ljust(6, '0')}Z"


def _deadline_error(field: str) -> ServiceError:
    return ServiceError(
        "invalid_deadline", "Publication deadline is not representable", 400, {"field": field}
    )


def deadline_chain(base_us: int, request: PublicationAdmissionV2) -> tuple[int, int]:
    """Validate the complete half-open publication deadline chain."""
    values: list[int] = []
    value = base_us
    stages = (
        ("bidding_deadline_seconds", request.bidding_deadline_seconds),
        ("acceptance_window_seconds", request.acceptance_window_seconds),
        ("execution_deadline_seconds", request.execution_deadline_seconds),
        ("review_deadline_seconds", request.review_deadline_seconds),
    )
    for index, (field, seconds) in enumerate(stages):
        if index in {2, 3}:
            value -= 1
        try:
            value = add_seconds_to_epoch_us(value, seconds)
            if not 0 <= value <= MAX_UTC_US:
                raise ValueError
            fixed_utc(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _deadline_error(field) from exc
        values.append(value)
    return values[0], values[1]


def inspect_v2_lock(
    cursor: sqlite3.Cursor,
    request: PublicationAdmissionV2,
) -> PublicationFinancialProof | None:
    proof = inspect_lock_facts(
        cursor,
        operation_id=request.lock_operation_id,
        payer_account_id=request.actor_id,
        task_id=request.task_id,
        amount=request.reward,
        require_locked=True,
    )
    if (
        proof is None
        and cursor.execute(
            "SELECT 1 FROM bank_escrow WHERE task_id=? LIMIT 1", (request.task_id,)
        ).fetchone()
        is not None
    ):
        raise PublicationEvidenceError("receipt_invalid")
    return proof


def materialize_publication(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
    request: PublicationAdmissionV2,
    proof: PublicationFinancialProof,
    *,
    clock_us: Callable[[], int],
    manifest_id: str,
) -> dict[str, object]:
    published_at_us = clock_us()
    committed_at_us = parse_utc_text_to_epoch_us(proof.committed_at)
    if committed_at_us < int(row[29]):
        raise PublicationEvidenceError("clock_out_of_range")
    if published_at_us < max(int(row[9]), int(row[25]), int(row[29]), committed_at_us):
        raise PublicationEvidenceError("clock_out_of_range")
    try:
        bidding_cutoff_us, acceptance_cutoff_us = deadline_chain(published_at_us, request)
    except ServiceError as exc:
        raise PublicationEvidenceError("clock_out_of_range") from exc
    published_at = fixed_utc(published_at_us)
    bidding_deadline = fixed_utc(bidding_cutoff_us)
    acceptance_deadline = fixed_utc(acceptance_cutoff_us)
    cursor.execute("SAVEPOINT v2_publication")
    cursor.execute(
        "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
        "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,bidding_deadline,"
        "bid_count,escrow_pending,escrow_id,created_at,dispute_id) "
        "VALUES (?,?,?,?,?,'open',?,?,?,?,0,0,?,?,NULL)",
        (
            request.task_id,
            request.actor_id,
            request.title,
            request.spec,
            request.reward,
            request.bidding_deadline_seconds,
            request.execution_deadline_seconds,
            request.review_deadline_seconds,
            bidding_deadline,
            proof.escrow_id,
            published_at,
        ),
    )
    _one(cursor)
    cursor.execute(
        "INSERT INTO board_task_contracts (task_id,lifecycle_version,timing_contract_version,"
        "acceptance_window_seconds,bidding_cutoff_us,acceptance_cutoff_us,execution_cutoff_us,"
        "review_cutoff_us,accepted_reward,accepted_bid_amount) "
        "VALUES (?,0,2,?,?,?,NULL,NULL,NULL,NULL)",
        (
            request.task_id,
            request.acceptance_window_seconds,
            bidding_cutoff_us,
            acceptance_cutoff_us,
        ),
    )
    _one(cursor)
    payload = json.dumps(
        {"bidding_deadline": bidding_deadline, "reward": request.reward, "title": request.title},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    cursor.execute(
        "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('task-board','task.created',?,?,?,?,?)",
        (
            published_at,
            request.task_id,
            request.actor_id,
            f"Task created: {request.title}",
            payload,
        ),
    )
    _one(cursor)
    if cursor.lastrowid is None or int(cursor.lastrowid) <= 0:
        raise sqlite3.OperationalError("event identity missing")
    event_id = int(cursor.lastrowid)
    cursor.execute(
        "INSERT INTO board_task_artifact_contracts (task_id,artifact_contract_version,"
        "input_provenance,accepted_binding_kind,base_request_sha256,input_manifest_id,"
        "input_manifest_sha256,publication_contract_sha256,accepted_publication_contract_sha256,"
        "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
        "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
        "input_actor_id) "
        "VALUES (?,1,'native_v6',NULL,?,?,?,?,NULL,NULL,NULL,NULL,?,'input',?)",
        (
            request.task_id,
            request.request_sha256,
            manifest_id,
            str(row[23]),
            str(row[24]),
            str(row[0]),
            request.actor_id,
        ),
    )
    _one(cursor)
    result: dict[str, object] = {
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
        "escrow_id": proof.escrow_id,
        "created_at": published_at,
        "bidding_deadline": bidding_deadline,
        "acceptance_deadline": acceptance_deadline,
        "bidding_cutoff_us": bidding_cutoff_us,
        "acceptance_cutoff_us": acceptance_cutoff_us,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": event_id,
    }
    cursor.execute(
        "UPDATE board_task_publications SET state='published',escrow_id=?,lock_request_sha256=?,"
        "lock_receipt_event_id=?,published_at_us=?,creation_event_id=?,result_json=?,"
        "error_json=NULL "
        "WHERE operation_id=? AND state='awaiting_lock'",
        (
            proof.escrow_id,
            proof.request_sha256,
            proof.receipt_event_id,
            published_at_us,
            event_id,
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ),
            str(row[0]),
        ),
    )
    _one(cursor)
    cursor.execute("RELEASE SAVEPOINT v2_publication")
    return result


def _one(cursor: sqlite3.Cursor) -> None:
    if cursor.rowcount != 1:
        raise sqlite3.OperationalError("publication write did not affect one row")


__all__ = [
    "MAX_UTC_US",
    "deadline_chain",
    "fixed_utc",
    "inspect_v2_lock",
    "materialize_publication",
]
