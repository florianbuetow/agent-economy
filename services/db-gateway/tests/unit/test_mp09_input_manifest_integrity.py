"""Input-manifest freeze integrity over ready upload identity."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    POSTER_ID,
    admission_payload,
    canonical_json,
    freeze_payload,
    full_snapshot,
    open_prototype_database,
    require_prepublication_api,
    seed_poster,
)
from tests.fakes.mp09_input_publication_fixture import (
    make_writer as make_prepublication_writer,
)
from tests.fakes.mp09_input_upload_fixture import (
    INPUT_BODY_A,
    InputClock,
    input_upload_payload,
    make_artifact_writer,
    parse_scoped_request,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

pytestmark = pytest.mark.unit


def _assert_scoped_fixture_before_capability_lookup() -> None:
    payload = input_upload_payload(
        operation_number=1,
        manifest_id="manifest-parser-probe",
        body=INPUT_BODY_A,
        display_filename="input.txt",
    )
    assert payload["protocol_version"] == 2
    assert payload["manifest_kind"] == "input"
    assert payload["actor_id"] == POSTER_ID
    assert payload["actor_role"] == "poster"
    assert payload["resource"].startswith("POST /task-publications/")
    assert all(
        field not in payload
        for field in (
            "expected_lifecycle_version",
            "accepted_binding_kind",
            "accepted_binding_sha256",
        )
    )
    parse_scoped_request(payload)


def _ready_input(
    tmp_path: Path,
) -> tuple[Any, sqlite3.Connection, Any, str, Any, dict[str, Any]]:
    _assert_scoped_fixture_before_capability_lookup()
    api = require_prepublication_api()
    connection = open_prototype_database(tmp_path / "input-manifest-integrity.sqlite3")
    seed_poster(connection)
    prepublication = make_prepublication_writer(api, connection)
    admission = api.requests.parse_publication_admission_v2(
        admission_payload(expected_input_count=1)
    )
    admitted = prepublication.admit(admission, verified_caller_kid=BOARD_KID)
    manifest_id = admitted["input_manifest_id"]
    assert isinstance(manifest_id, str) and manifest_id
    request = parse_scoped_request(
        input_upload_payload(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
    )
    artifacts = make_artifact_writer(connection, InputClock())
    reserved = artifacts.reserve_upload(request, verified_caller_kid=BOARD_KID)
    ready = artifacts.finalize_upload(
        request.upload_operation_id,
        owner_id=reserved["attempt"]["owner_id"],
        generation=reserved["attempt"]["generation"],
        attempt_nonce=reserved["attempt"]["attempt_nonce"],
        observed_size_bytes=request.declared_size_bytes,
        observed_sha256=request.content_sha256,
        verified_caller_kid=BOARD_KID,
    )
    assert ready["state"] == "ready"
    return api, connection, prepublication, manifest_id, request, ready


@pytest.mark.parametrize("tamper", ["canonical_only", "content_hash_projection"])
def test_freeze_rejects_ready_input_request_identity_tampering_without_any_mutation(
    tmp_path: Path,
    tamper: str,
) -> None:
    api, connection, prepublication, manifest_id, request, _ready = _ready_input(tmp_path)
    try:
        if tamper == "canonical_only":
            connection.execute(
                "UPDATE board_artifact_uploads SET canonical_request_json=? "
                "WHERE upload_operation_id=?",
                (canonical_json({"tampered": "input-upload"}), request.upload_operation_id),
            )
        else:
            connection.execute(
                "UPDATE board_artifact_uploads SET content_sha256=? WHERE upload_operation_id=?",
                ("f" * 64, request.upload_operation_id),
            )
        connection.commit()
        freeze = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            prepublication.freeze_inputs(freeze, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "publication_state_corrupt",
        )
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_valid_ready_input_freezes_exact_uncorrupted_request_identity(tmp_path: Path) -> None:
    api, connection, prepublication, manifest_id, request, ready = _ready_input(tmp_path)
    try:
        freeze = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
        frozen = prepublication.freeze_inputs(freeze, verified_caller_kid=BOARD_KID)
        row = connection.execute(
            "SELECT canonical_manifest_json,manifest_sha256 FROM board_artifact_manifests "
            "WHERE manifest_id=?",
            (manifest_id,),
        ).fetchone()
        assert row is not None
        canonical = str(row[0])
        assert str(row[1]) == hashlib.sha256(canonical.encode()).hexdigest()
        decoded: object = json.loads(canonical)
        assert isinstance(decoded, dict)
        artifacts = decoded.get("artifacts")
        assert isinstance(artifacts, list) and len(artifacts) == 1
        artifact = artifacts[0]
        assert isinstance(artifact, dict)
        assert artifact["artifact_id"] == ready["artifact_id"]
        assert artifact["display_filename"] == request.display_filename
        assert artifact["byte_size"] == request.declared_size_bytes
        assert artifact["sha256"] == request.content_sha256
        assert frozen["publication_state"] == "awaiting_publish"
        assert frozen["input_manifest_sha256"] == str(row[1])
    finally:
        connection.close()
