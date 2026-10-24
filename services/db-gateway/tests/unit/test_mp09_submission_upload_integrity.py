"""Submission freeze rejects corrupted ready-upload identity."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_v6_artifact_fixture import BOARD_KID
from tests.unit.test_mp09_submission_manifest import (
    _MANIFEST_ID,
    _SUBMITTED_AT_US,
    _open_submission_database,
    _parse_submission_v2,
    _ready_artifact,
    _submission_payload,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

pytestmark = pytest.mark.unit


def _full_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[object, ...]]]:
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    return {
        table: [
            tuple(row)
            for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY 1')  # nosec B608
        ]
        for table in tables
    }


@pytest.mark.parametrize("tamper", ["canonical_only", "content_hash_projection"])
def test_submission_rejects_corrupted_ready_upload_identity_without_any_mutation(
    tmp_path: Path,
    tamper: str,
) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        ready = _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        if tamper == "canonical_only":
            connection.execute(
                "UPDATE board_artifact_uploads SET canonical_request_json=? WHERE artifact_id=?",
                ('{"tampered":"worker-upload"}', ready["artifact_id"]),
            )
        else:
            connection.execute(
                "UPDATE board_artifact_uploads SET content_sha256=? WHERE artifact_id=?",
                ("f" * 64, ready["artifact_id"]),
            )
        connection.commit()

        request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        before = _full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "artifact_state_corrupt",
        )
        assert _full_snapshot(connection) == before
    finally:
        connection.close()


def test_valid_ready_upload_freezes_as_exact_one_file_submission(tmp_path: Path) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        ready = _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        result = operations.claim_operation(request, BOARD_KID)

        manifest = connection.execute(
            "SELECT state,canonical_manifest_json,manifest_sha256 FROM "
            "board_artifact_manifests WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        assert manifest is not None
        canonical = str(manifest[1])
        assert tuple(manifest)[::2] == (
            "frozen",
            hashlib.sha256(canonical.encode()).hexdigest(),
        )
        decoded: object = json.loads(canonical)
        assert isinstance(decoded, dict)
        items = decoded.get("artifacts")
        assert isinstance(items, list) and len(items) == 1
        item = items[0]
        assert isinstance(item, dict)
        assert item == {
            "artifact_id": ready["artifact_id"],
            "display_filename": "answer.txt",
            "evidence_format": "text",
            "media_type": "text/plain; charset=utf-8",
            "byte_size": len(b"answer\n"),
            "sha256": hashlib.sha256(b"answer\n").hexdigest(),
            "provenance": "v6_upload",
            "actor_id": "worker-1",
            "uploaded_at": item["uploaded_at"],
        }
        assert result["result"]["manifest_sha256"] == str(manifest[2])
        assert result["result"]["status"] == "submitted"
    finally:
        connection.close()
