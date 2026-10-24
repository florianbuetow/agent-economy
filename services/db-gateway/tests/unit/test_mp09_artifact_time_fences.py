"""Artifact ownership uses the same half-open cutoffs as task execution."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    EXECUTION_CUTOFF_US,
    TASK_ID,
    MutableClock,
    attempt_fence,
    database_snapshot,
    explicit_policy,
    make_writer,
    open_v6_database,
    require_v6_api,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit


@contextmanager
def _scenario(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, MutableClock, Any, Any]]:
    api = require_v6_api()
    connection = open_v6_database(tmp_path / "time-fences.sqlite3")
    try:
        seed_accepted_task(connection, task_id=TASK_ID, manifest_id="manifest-v6-reservation")
        clock = MutableClock()
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = api.requests.parse_artifact_upload_request(
            upload_payload(
                operation_id="40000000-0000-4000-8000-000000000001",
                task_id=TASK_ID,
                manifest_id="manifest-v6-reservation",
            )
        )
        yield connection, clock, writer, request
    finally:
        connection.close()


def _invoke(writer: Any, method: str, request: Any, reservation: dict[str, Any]) -> Any:
    if method == "reserve":
        return writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
    if method == "renew":
        return writer.renew_upload(
            request.upload_operation_id,
            **attempt_fence(reservation),
            verified_caller_kid=BOARD_KID,
        )
    assert method == "finalize"
    return writer.finalize_upload(
        request.upload_operation_id,
        **attempt_fence(reservation),
        observed_size_bytes=request.declared_size_bytes,
        observed_sha256=request.content_sha256,
        verified_caller_kid=BOARD_KID,
    )


@pytest.mark.parametrize("method", ["reserve", "renew", "finalize"])
def test_exact_absolute_task_cutoff_closes_every_writable_fence(
    tmp_path: Path, method: str
) -> None:
    with _scenario(tmp_path) as (connection, clock, writer, request):
        clock.now_us = EXECUTION_CUTOFF_US - 120_000_000
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        assert reservation["attempt"]["absolute_upload_deadline_us"] == EXECUTION_CUTOFF_US
        assert reservation["attempt"]["lease_expires_at_us"] == EXECUTION_CUTOFF_US
        clock.now_us = EXECUTION_CUTOFF_US
        before = database_snapshot(connection)

        with pytest.raises(ServiceError) as raised:
            _invoke(writer, method, request, reservation)

        assert (raised.value.status_code, raised.value.error) == (409, "upload_deadline_exceeded")
        assert database_snapshot(connection) == before


@pytest.mark.parametrize("method", ["renew", "finalize"])
def test_exact_lease_expiry_rejects_previous_owner_without_mutation(
    tmp_path: Path, method: str
) -> None:
    with _scenario(tmp_path) as (connection, clock, writer, request):
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = reservation["attempt"]["lease_expires_at_us"]
        assert clock.now_us < reservation["attempt"]["absolute_upload_deadline_us"]
        before = database_snapshot(connection)

        with pytest.raises(ServiceError) as raised:
            _invoke(writer, method, request, reservation)

        assert (raised.value.status_code, raised.value.error) == (409, "upload_lease_expired")
        assert database_snapshot(connection) == before


def test_exact_lease_expiry_allows_new_owner_with_original_absolute_deadline(
    tmp_path: Path,
) -> None:
    with _scenario(tmp_path) as (connection, clock, writer, request):
        original = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = original["attempt"]["lease_expires_at_us"]
        replacement = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)

        assert replacement["artifact_id"] == original["artifact_id"]
        assert replacement["object_key"] == original["object_key"]
        assert replacement["attempt"]["owner_id"] != original["attempt"]["owner_id"]
        assert replacement["attempt"]["generation"] == original["attempt"]["generation"] + 1
        assert (
            replacement["attempt"]["absolute_upload_deadline_us"]
            == (original["attempt"]["absolute_upload_deadline_us"])
        )
        rows = connection.execute(
            "SELECT state FROM board_artifact_upload_attempts ORDER BY generation"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("expired",), ("active",)]
