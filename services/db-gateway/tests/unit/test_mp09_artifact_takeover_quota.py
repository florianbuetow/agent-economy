"""Takeover preserves the existing reservation's count and byte quota slot."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    BODY_A,
    TASK_ID,
    MutableClock,
    explicit_policy,
    make_writer,
    open_v6_database,
    require_v6_api,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
def test_takeover_at_full_count_and_byte_quota_reuses_original_slot(tmp_path: Path) -> None:
    api = require_v6_api()
    connection = open_v6_database(tmp_path / "full-quota.sqlite3")
    task_id = TASK_ID
    manifest_id = "manifest-v6-reservation"
    try:
        seed_accepted_task(connection, task_id=task_id, manifest_id=manifest_id)
        clock = MutableClock()
        writer = make_writer(
            api,
            connection,
            clock,
            explicit_policy(
                api,
                max_file_bytes=len(BODY_A),
                max_files_per_manifest=1,
                max_manifest_bytes=len(BODY_A),
            ),
        )
        request = api.requests.parse_artifact_upload_request(
            upload_payload(
                operation_id="30000000-0000-4000-8000-000000000001",
                task_id=task_id,
                manifest_id=manifest_id,
            )
        )
        original = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = original["attempt"]["lease_expires_at_us"] + 1
        replacement = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)

        assert replacement["artifact_id"] == original["artifact_id"]
        assert replacement["object_key"] == original["object_key"]
        assert replacement["attempt"]["generation"] == original["attempt"]["generation"] + 1
        assert replacement["attempt"]["owner_id"] != original["attempt"]["owner_id"]
        quota = connection.execute(
            "SELECT COUNT(*),SUM(declared_size_bytes) FROM board_artifact_uploads "
            "WHERE manifest_id=? AND state IN ('reserved','ready')",
            (manifest_id,),
        ).fetchone()
        assert quota is not None
        assert tuple(quota) == (1, len(BODY_A))
        attempts = connection.execute(
            "SELECT state FROM board_artifact_upload_attempts ORDER BY generation"
        ).fetchall()
        assert [tuple(row) for row in attempts] == [("expired",), ("active",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
