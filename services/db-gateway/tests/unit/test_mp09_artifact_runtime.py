"""Lane T: runtime wiring of V6 artifact uploads, manifests, recovery and archive."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services import artifact_runtime as runtime
from db_gateway_service.services.artifact_submission import freeze_submission_manifest
from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    BODY_A,
    BODY_B,
    HASH_A,
    HASH_B,
    NATIVE_BINDING,
    NOW_US,
    TASK_ID,
    MutableClock,
    open_v6_database,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

pytestmark = pytest.mark.unit

_POLICY: dict[str, object] = {
    "contract_version": 1,
    "max_file_bytes": 32,
    "max_files_per_manifest": 2,
    "max_manifest_bytes": 32,
    "reservation_ttl_seconds": 300,
    "reservation_renew_before_expiry_seconds": 60,
    "max_upload_duration_seconds": 900,
}
_MANIFEST_ID = runtime.submission_manifest_id(TASK_ID)
_OP_A = "11111111-1111-4111-8111-111111111111"
_OP_B = "22222222-2222-4222-8222-222222222222"
_OP_C = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    connection = open_v6_database(tmp_path / "economy.db")
    seed_accepted_task(connection, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
    return connection


def _reserve(
    db: sqlite3.Connection,
    clock: MutableClock,
    operation_id: str,
    *,
    body: bytes = BODY_A,
    sha256: str = HASH_A,
    filename: str = "answer.txt",
) -> dict[str, Any]:
    return runtime.reserve_artifact_upload(
        db,
        {
            "policy": dict(_POLICY),
            "request": upload_payload(
                operation_id=operation_id,
                task_id=TASK_ID,
                manifest_id=_MANIFEST_ID,
                body=body,
                content_sha256=sha256,
                display_filename=filename,
            ),
        },
        caller_kid=BOARD_KID,
        clock_us=clock,
        current_task_board_caller_kid=BOARD_KID,
    )


def _finalize(
    db: sqlite3.Connection,
    clock: MutableClock,
    reservation: dict[str, Any],
    *,
    size: int,
    sha256: str,
) -> dict[str, Any]:
    attempt = reservation["attempt"]
    return runtime.finalize_artifact_upload(
        db,
        reservation["upload_operation_id"],
        {
            "policy": dict(_POLICY),
            "owner_id": attempt["owner_id"],
            "generation": attempt["generation"],
            "attempt_nonce": attempt["attempt_nonce"],
            "observed_size_bytes": size,
            "observed_sha256": sha256,
        },
        caller_kid=BOARD_KID,
        clock_us=clock,
        current_task_board_caller_kid=BOARD_KID,
    )


def test_context_reports_governed_binding_and_submission_manifest(
    db: sqlite3.Connection,
) -> None:
    context = runtime.task_artifact_context(db, TASK_ID)
    assert context == {
        "task_id": TASK_ID,
        "governed": True,
        "status": "accepted",
        "worker_id": "worker-1",
        "lifecycle_version": 1,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": NATIVE_BINDING,
        "submission_manifest_id": _MANIFEST_ID,
        "submission_manifest_state": "draft",
    }


def test_context_unknown_task_is_404(db: sqlite3.Connection) -> None:
    with pytest.raises(ServiceError) as raised:
        runtime.task_artifact_context(db, "t-missing")
    assert raised.value.status_code == 404


def test_reserve_creates_missing_draft_submission_manifest(db: sqlite3.Connection) -> None:
    db.execute("DELETE FROM board_artifact_manifests WHERE manifest_id=?", (_MANIFEST_ID,))
    db.commit()
    reservation = _reserve(db, MutableClock(), _OP_A)
    assert reservation["state"] == "reserved"
    row = db.execute(
        "SELECT kind,actor_id,state,public_task_id FROM board_artifact_manifests "
        "WHERE manifest_id=?",
        (_MANIFEST_ID,),
    ).fetchone()
    assert tuple(row) == ("submission", "worker-1", "draft", TASK_ID)


def test_reserve_rejects_foreign_manifest_id(db: sqlite3.Connection) -> None:
    payload = upload_payload(operation_id=_OP_A, task_id=TASK_ID, manifest_id="manifest-other")
    with pytest.raises(ServiceError) as raised:
        runtime.reserve_artifact_upload(
            db,
            {"policy": dict(_POLICY), "request": payload},
            caller_kid=BOARD_KID,
            clock_us=MutableClock(),
            current_task_board_caller_kid=BOARD_KID,
        )
    assert raised.value.error == "manifest_mismatch"
    assert db.execute("SELECT COUNT(*) FROM board_artifact_uploads").fetchone()[0] == 0


def test_reserve_rejected_actor_leaves_no_upload_row(db: sqlite3.Connection) -> None:
    payload = upload_payload(operation_id=_OP_A, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
    payload["actor_id"] = "stranger"
    with pytest.raises(ServiceError) as raised:
        runtime.reserve_artifact_upload(
            db,
            {"policy": dict(_POLICY), "request": payload},
            caller_kid=BOARD_KID,
            clock_us=MutableClock(),
            current_task_board_caller_kid=BOARD_KID,
        )
    assert raised.value.status_code in {403, 409}
    assert db.execute("SELECT COUNT(*) FROM board_artifact_uploads").fetchone()[0] == 0


def test_policy_is_required_without_defaults(db: sqlite3.Connection) -> None:
    payload = upload_payload(operation_id=_OP_A, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
    with pytest.raises(ServiceError) as raised:
        runtime.reserve_artifact_upload(
            db,
            {"request": payload},
            caller_kid=BOARD_KID,
            clock_us=MutableClock(),
            current_task_board_caller_kid=BOARD_KID,
        )
    assert raised.value.status_code == 400


def test_two_file_manifest_freezes_exact_hashes_and_blocks_new_uploads(
    db: sqlite3.Connection,
) -> None:
    clock = MutableClock()
    first = _reserve(db, clock, _OP_A)
    _finalize(db, clock, first, size=len(BODY_A), sha256=HASH_A)
    second = _reserve(db, clock, _OP_B, body=BODY_B, sha256=HASH_B, filename="notes.txt")
    _finalize(db, clock, second, size=len(BODY_B), sha256=HASH_B)

    listing = runtime.list_task_artifacts(db, TASK_ID)
    hashes = {item["filename"]: item["content_hash"] for item in listing["artifacts"]}
    assert hashes == {"answer.txt": HASH_A, "notes.txt": HASH_B}
    assert all(item["asset_id"].startswith("asset-") for item in listing["artifacts"])

    class _Request:
        task_id = TASK_ID
        expected_lifecycle_version = 1

        @staticmethod
        def argument(name: str) -> object:
            return {
                "manifest_id": _MANIFEST_ID,
                "worker_id": "worker-1",
                "accepted_binding_kind": "publication_contract",
                "accepted_binding_sha256": NATIVE_BINDING,
            }[name]

    cursor = db.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    frozen = freeze_submission_manifest(cursor, _Request(), NOW_US)  # type: ignore[arg-type]
    db.commit()

    listing = runtime.list_task_artifacts(db, TASK_ID)
    manifest = next(item for item in listing["manifests"] if item["kind"] == "submission")
    assert manifest["state"] == "frozen"
    assert manifest["manifest_sha256"] == frozen.manifest_sha256
    canonical = manifest["canonical_manifest_json"]
    assert hashlib.sha256(canonical.encode()).hexdigest() == frozen.manifest_sha256
    frozen_hashes = sorted(item["sha256"] for item in json.loads(canonical)["artifacts"])
    assert frozen_hashes == sorted([HASH_A, HASH_B])

    with pytest.raises(ServiceError) as raised:
        _reserve(db, clock, _OP_C)
    assert raised.value.error == "manifest_mismatch"


def test_recovery_listing_exposes_reserved_fence_and_referenced_keys(
    db: sqlite3.Connection,
) -> None:
    clock = MutableClock()
    ready = _reserve(db, clock, _OP_A)
    _finalize(db, clock, ready, size=len(BODY_A), sha256=HASH_A)
    pending = _reserve(db, clock, _OP_B, body=BODY_B, sha256=HASH_B, filename="b.txt")

    listing = runtime.artifact_recovery_listing(db, caller_kid=BOARD_KID)
    assert [item["upload_operation_id"] for item in listing["reserved"]] == [_OP_B]
    reserved = listing["reserved"][0]
    assert reserved["attempt"]["attempt_nonce"] == pending["attempt"]["attempt_nonce"]
    assert reserved["object_key"] == pending["object_key"]
    assert reserved["content_sha256"] == HASH_B
    assert json.loads(reserved["canonical_request_json"])["upload_operation_id"] == _OP_B
    assert ready["object_key"] in listing["referenced_object_keys"]
    assert pending["object_key"] in listing["referenced_object_keys"]


def test_orphan_releases_reservation(db: sqlite3.Connection) -> None:
    clock = MutableClock()
    pending = _reserve(db, clock, _OP_A)
    attempt = pending["attempt"]
    result = runtime.orphan_artifact_upload(
        db,
        _OP_A,
        {
            "policy": dict(_POLICY),
            "owner_id": attempt["owner_id"],
            "generation": attempt["generation"],
            "attempt_nonce": attempt["attempt_nonce"],
            "failure_code": "upload_abandoned",
        },
        caller_kid=BOARD_KID,
        clock_us=clock,
        current_task_board_caller_kid=BOARD_KID,
    )
    assert result["state"] == "orphaned"
    listing = runtime.artifact_recovery_listing(db, caller_kid=BOARD_KID)
    assert listing["reserved"] == []
    assert pending["object_key"] not in listing["referenced_object_keys"]


def _make_terminal(db: sqlite3.Connection, terminal_at_us: int) -> None:
    db.execute(
        "UPDATE board_tasks SET status='approved', approved_at=? WHERE task_id=?",
        (epoch_us_to_utc_text(terminal_at_us), TASK_ID),
    )
    db.commit()


def test_archive_candidates_respect_configured_age_and_finalize_moves_pointer(
    db: sqlite3.Connection,
) -> None:
    clock = MutableClock()
    ready = _reserve(db, clock, _OP_A)
    _finalize(db, clock, ready, size=len(BODY_A), sha256=HASH_A)
    day_us = 86_400 * 1_000_000
    _make_terminal(db, NOW_US)

    young = runtime.archive_candidates(
        db, archive_after_seconds=3 * 86_400, limit=10, now_us=NOW_US + 2 * day_us
    )
    assert young == {"candidates": []}

    old = runtime.archive_candidates(
        db, archive_after_seconds=3 * 86_400, limit=10, now_us=NOW_US + 4 * day_us
    )
    assert len(old["candidates"]) == 1
    candidate = old["candidates"][0]
    assert candidate["kind"] == "artifact"
    assert candidate["live_object_key"] == ready["object_key"]
    assert candidate["archive_object_key"] == "archive/" + ready["object_key"].removeprefix("live/")

    finalized = runtime.finalize_archive(db, candidate, clock_us=lambda: NOW_US + 4 * day_us)
    assert finalized["storage_path"] == candidate["archive_object_key"]
    replay = runtime.finalize_archive(db, candidate, clock_us=lambda: NOW_US + 4 * day_us)
    assert replay == finalized

    listing = runtime.list_task_artifacts(db, TASK_ID)
    assert listing["artifacts"][0]["storage_path"] == candidate["archive_object_key"]
    assert listing["artifacts"][0]["state"] == "archived"
    assert runtime.archive_candidates(
        db, archive_after_seconds=3 * 86_400, limit=10, now_us=NOW_US + 4 * day_us
    ) == {"candidates": []}
    keys = runtime.artifact_recovery_listing(db, caller_kid=BOARD_KID)["referenced_object_keys"]
    assert candidate["archive_object_key"] in keys
    assert candidate["live_object_key"] not in keys


def test_archive_finalize_rejects_mismatched_hash(db: sqlite3.Connection) -> None:
    clock = MutableClock()
    ready = _reserve(db, clock, _OP_A)
    _finalize(db, clock, ready, size=len(BODY_A), sha256=HASH_A)
    _make_terminal(db, NOW_US)
    candidate = runtime.archive_candidates(
        db, archive_after_seconds=1, limit=10, now_us=NOW_US + 5_000_000
    )["candidates"][0]
    candidate = {**candidate, "content_hash": HASH_B}
    with pytest.raises(ServiceError) as raised:
        runtime.finalize_archive(db, candidate, clock_us=lambda: NOW_US + 5_000_000)
    assert raised.value.status_code == 409


def test_legacy_asset_is_archived_by_storage_path(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO board_assets (asset_id,task_id,uploader_id,filename,content_type,"
        "size_bytes,storage_path,content_hash,uploaded_at) VALUES "
        "('asset-legacy',?,'worker-1','a.txt','text/plain',?,'live/ab/cd/x.blob',?,"
        "'2099-01-02T00:00:00Z')",
        (TASK_ID, len(BODY_A), HASH_A),
    )
    db.commit()
    _make_terminal(db, NOW_US)
    candidates = runtime.archive_candidates(
        db, archive_after_seconds=1, limit=10, now_us=NOW_US + 5_000_000
    )["candidates"]
    legacy = next(item for item in candidates if item["kind"] == "asset")
    assert legacy["archive_object_key"] == "archive/ab/cd/x.blob"
    runtime.finalize_archive(db, legacy, clock_us=lambda: NOW_US + 5_000_000)
    path = db.execute(
        "SELECT storage_path FROM board_assets WHERE asset_id='asset-legacy'"
    ).fetchone()[0]
    assert path == "archive/ab/cd/x.blob"
