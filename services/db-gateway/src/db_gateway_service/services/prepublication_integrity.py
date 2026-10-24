"""Persisted-state integrity checks for V2 prepublication rows."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.services.artifact_manifest import (
    UPLOAD_COLUMNS,
    canonical_manifest,
    validate_upload_row,
)
from db_gateway_service.services.prepublication_receipt import deadline_chain, fixed_utc
from db_gateway_service.task_publication_v2_requests import (
    parse_input_manifest_freeze_v2,
    parse_publication_admission_v2,
    parse_publication_publish_v2,
)
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _corrupt() -> ServiceError:
    return ServiceError(
        "publication_state_corrupt",
        "Persisted prepublication state failed integrity validation",
        500,
        {},
    )


def _decoded(raw: object) -> object:
    if not isinstance(raw, str):
        raise _corrupt()
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise _corrupt() from exc


def _admission(row: tuple[Any, ...]) -> Any:
    admission = parse_publication_admission_v2(_decoded(row[3]))
    if (
        int(row[17]) != 2
        or admission.canonical_request_json != row[3]
        or admission.request_sha256 != row[4]
        or admission.base_request_sha256 != row[19]
        or admission.publication_operation_id != row[0]
        or admission.task_id != row[2]
        or admission.actor_id != row[6]
        or admission.lock_operation_id != row[7]
        or admission.expected_input_count != row[18]
    ):
        raise _corrupt()
    return admission


def _manifest(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> tuple[Any, ...]:
    manifest = cursor.execute(
        "SELECT manifest_id,task_id,kind,actor_id,state,canonical_manifest_json,"
        "manifest_sha256,frozen_at_us,publication_operation_id,public_task_id "
        "FROM board_artifact_manifests WHERE publication_operation_id=? AND kind='input'",
        (str(row[0]),),
    ).fetchone()
    if (
        manifest is None
        or str(manifest[1]) != str(row[2])
        or str(manifest[2]) != "input"
        or str(manifest[3]) != str(row[6])
        or str(manifest[8]) != str(row[0])
        or manifest[9] is not None
    ):
        raise _corrupt()
    return tuple(manifest)


def _assert_freeze(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
    manifest: tuple[Any, ...],
    admission: Any,
) -> None:
    if row[20] is None:
        if (
            any(row[index] is not None for index in (21, 22, 23, 24, 25))
            or str(manifest[4]) != "draft"
            or any(manifest[index] is not None for index in (5, 6, 7))
        ):
            raise _corrupt()
        return
    freeze = parse_input_manifest_freeze_v2(_decoded(row[21]))
    if (
        freeze.canonical_freeze_request_json != row[21]
        or freeze.freeze_request_sha256 != row[22]
        or freeze.freeze_operation_id != row[20]
        or freeze.publication_operation_id != row[0]
        or freeze.task_id != row[2]
        or freeze.actor_id != row[6]
        or freeze.input_manifest_id != manifest[0]
    ):
        raise _corrupt()
    ready_rows = cursor.execute(
        f"SELECT {UPLOAD_COLUMNS} FROM board_artifact_uploads "  # nosec B608
        "WHERE manifest_id=? AND state='ready' ORDER BY artifact_id",
        (str(manifest[0]),),
    ).fetchall()
    uploads = [tuple(ready_row) for ready_row in ready_rows]
    for upload in uploads:
        validate_upload_row(upload, expected_caller_kid=str(row[5]))
    manifest_json, manifest_sha256 = canonical_manifest(
        manifest_id=str(manifest[0]),
        task_id=str(row[2]),
        kind="input",
        actor_id=str(row[6]),
        uploads=uploads,
    )
    contract_sha256 = _sha256(
        {
            "artifact_contract_version": 1,
            "base_request_sha256": admission.base_request_sha256,
            "input_manifest_sha256": manifest_sha256,
        }
    )
    if (
        str(manifest[4]) != "frozen"
        or manifest[5] != manifest_json
        or manifest[6] != manifest_sha256
        or manifest[7] != row[25]
        or row[23] != manifest_sha256
        or row[24] != contract_sha256
    ):
        raise _corrupt()


def _assert_publish(row: tuple[Any, ...]) -> None:
    if row[26] is None:
        if any(row[index] is not None for index in (27, 28, 29)):
            raise _corrupt()
        return
    publish = parse_publication_publish_v2(_decoded(row[27]))
    if (
        publish.canonical_publish_request_json != row[27]
        or publish.publish_request_sha256 != row[28]
        or publish.publish_operation_id != row[26]
        or publish.publication_operation_id != row[0]
        or publish.task_id != row[2]
        or publish.actor_id != row[6]
        or publish.lock_operation_id != row[7]
        or publish.publication_contract_sha256 != row[24]
    ):
        raise _corrupt()


def _assert_published(cursor: sqlite3.Cursor, row: tuple[Any, ...], admission: Any) -> None:
    if str(row[8]) != "published":
        return
    task = cursor.execute(
        "SELECT poster_id,title,spec,reward,bidding_deadline_seconds,deadline_seconds,"
        "review_deadline_seconds,bidding_deadline,escrow_id,created_at FROM board_tasks "
        "WHERE task_id=?",
        (str(row[2]),),
    ).fetchone()
    timing = cursor.execute(
        "SELECT timing_contract_version,acceptance_window_seconds,bidding_cutoff_us,"
        "acceptance_cutoff_us FROM board_task_contracts WHERE task_id=?",
        (str(row[2]),),
    ).fetchone()
    creation = cursor.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
        "FROM events WHERE event_id=?",
        (row[14],),
    ).fetchone()
    lock_event = cursor.execute(
        "SELECT event_source,event_type,timestamp,task_id,agent_id FROM events WHERE event_id=?",
        (row[12],),
    ).fetchone()
    artifact = cursor.execute(
        "SELECT artifact_contract_version,input_provenance,base_request_sha256,input_manifest_id,"
        "input_manifest_sha256,publication_contract_sha256,publication_operation_id,"
        "input_manifest_kind,input_actor_id,source_publication_request_sha256 "
        "FROM board_task_artifact_contracts WHERE task_id=?",
        (str(row[2]),),
    ).fetchone()
    if None in {task, timing, creation, lock_event, artifact}:
        raise _corrupt()
    assert task is not None and timing is not None and creation is not None
    assert lock_event is not None and artifact is not None
    published_at = fixed_utc(int(row[13]))
    bidding_cutoff, acceptance_cutoff = deadline_chain(int(row[13]), admission)
    bidding_deadline = fixed_utc(bidding_cutoff)
    acceptance_deadline = fixed_utc(acceptance_cutoff)
    expected_payload = _canonical(
        {"bidding_deadline": bidding_deadline, "reward": admission.reward, "title": admission.title}
    )
    expected_result = {
        "task_id": admission.task_id,
        "poster_id": admission.actor_id,
        "title": admission.title,
        "spec": admission.spec,
        "reward": admission.reward,
        "bidding_deadline_seconds": admission.bidding_deadline_seconds,
        "acceptance_window_seconds": admission.acceptance_window_seconds,
        "execution_deadline_seconds": admission.execution_deadline_seconds,
        "review_deadline_seconds": admission.review_deadline_seconds,
        "status": "open",
        "escrow_id": str(row[10]),
        "created_at": published_at,
        "bidding_deadline": bidding_deadline,
        "acceptance_deadline": acceptance_deadline,
        "bidding_cutoff_us": bidding_cutoff,
        "acceptance_cutoff_us": acceptance_cutoff,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": int(row[14]),
    }
    try:
        stored_result = json.loads(str(row[15]))
        lock_time = parse_utc_text_to_epoch_us(str(lock_event[2]))
    except (TypeError, ValueError) as exc:
        raise _corrupt() from exc
    if (
        tuple(task)
        != (
            admission.actor_id,
            admission.title,
            admission.spec,
            admission.reward,
            admission.bidding_deadline_seconds,
            admission.execution_deadline_seconds,
            admission.review_deadline_seconds,
            bidding_deadline,
            str(row[10]),
            published_at,
        )
        or tuple(timing)
        != (2, admission.acceptance_window_seconds, bidding_cutoff, acceptance_cutoff)
        or tuple(creation)
        != (
            "task-board",
            "task.created",
            published_at,
            admission.task_id,
            admission.actor_id,
            f"Task created: {admission.title}",
            expected_payload,
        )
        or lock_event[0] != "central-bank"
        or lock_event[1] != "escrow.locked"
        or lock_event[3] not in {None, admission.task_id}
        or lock_event[4] != admission.actor_id
        or not int(row[9]) <= int(row[25]) <= int(row[29]) <= lock_time <= int(row[13])
        or tuple(artifact)
        != (
            1,
            "native_v6",
            admission.request_sha256,
            str(artifact[3]),
            str(row[23]),
            str(row[24]),
            str(row[0]),
            "input",
            admission.actor_id,
            None,
        )
        or stored_result != expected_result
        or row[15] != _canonical(expected_result)
        or row[11]
        != canonical_request(
            "escrow_lock",
            {
                "payer_account_id": admission.actor_id,
                "task_id": admission.task_id,
                "amount": admission.reward,
            },
        ).request_sha256
    ):
        raise _corrupt()


def _assert_blocked(row: tuple[Any, ...]) -> None:
    if str(row[8]) != "blocked_integrity":
        return
    if any(row[index] is not None for index in range(10, 16)):
        raise _corrupt()
    try:
        error: object = json.loads(str(row[16]))
    except (TypeError, ValueError) as exc:
        raise _corrupt() from exc
    reasons = {
        "receipt_invalid",
        "escrow_invalid",
        "settlement_conflict",
        "publication_conflict",
        "clock_out_of_range",
    }
    if not isinstance(error, dict) or error != {
        "error": "publication_integrity_blocked",
        "message": "Publication evidence requires reconciliation",
        "details": error.get("details") if isinstance(error, dict) else None,
    }:
        raise _corrupt()
    details = error.get("details")
    if (
        not isinstance(details, dict)
        or set(details) != {"reason"}
        or details["reason"] not in reasons
    ):
        raise _corrupt()
    if row[16] != _canonical(error):
        raise _corrupt()


def assert_publication_integrity(
    cursor: sqlite3.Cursor,
    row: tuple[Any, ...],
) -> None:
    """Recompute every persisted V2 command and frozen-manifest identity."""
    try:
        admission = _admission(row)
        admitted = int(row[9])
        if row[25] is not None and int(row[25]) < admitted:
            raise _corrupt()
        if row[29] is not None and (row[25] is None or int(row[29]) < int(row[25])):
            raise _corrupt()
        manifest = _manifest(cursor, row)
        _assert_freeze(cursor, row, manifest, admission)
        _assert_publish(row)
        _assert_published(cursor, row, admission)
        _assert_blocked(row)
    except ServiceError as exc:
        if exc.error == "publication_state_corrupt":
            raise
        raise _corrupt() from exc
    except (IndexError, TypeError, ValueError) as exc:
        raise _corrupt() from exc


__all__ = ["assert_publication_integrity"]
