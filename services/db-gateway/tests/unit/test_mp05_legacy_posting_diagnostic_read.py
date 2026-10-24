"""Read-only diagnostics for one caller-owned legacy posting observation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.legacy_posting_writer import LegacyPostingWriter
from tests.unit.test_mp05_legacy_posting_recovery import (
    _open_copy,
    _PendingTemplate,
    _persist_conflicting_refund,
    _persist_exact_refund,
    _reconcile,
    pending_template,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit
__all__ = ["pending_template"]

_CAPABILITY_GAP = "Missing read-only legacy posting observation diagnostic API"


def _database_snapshot(connection: sqlite3.Connection) -> tuple[tuple[str, ...], int]:
    return tuple(connection.iterdump()), connection.total_changes


def _get(writer: DbWriter, observation_id: str, caller_kid: str) -> dict[str, Any]:
    method = getattr(writer, "get_legacy_posting_observation", None)
    if not callable(method):
        pytest.fail(_CAPABILITY_GAP)
    return cast("dict[str, Any]", method(observation_id, caller_kid))


def _assert_read_is_exact_and_inert(
    writer: DbWriter,
    observation_id: str,
    caller_kid: str,
    expected: dict[str, Any],
) -> None:
    before = _database_snapshot(writer.connection)
    assert _get(writer, observation_id, caller_kid) == expected
    assert _database_snapshot(writer.connection) == before


def test_legacy_posting_writer_exposes_dedicated_read_method(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-capability.db")
    try:
        assert callable(getattr(LegacyPostingWriter(writer.connection), "get", None)), (
            _CAPABILITY_GAP
        )
    finally:
        writer.close()


def test_read_pending_returns_exact_existing_envelope_without_writes(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-pending.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        expected = _reconcile(writer, observation_id, pending_template.caller_kid)
        assert expected["observation_state"] == "refund_pending"
        _assert_read_is_exact_and_inert(
            writer, observation_id, pending_template.caller_kid, expected
        )
    finally:
        writer.close()


def test_read_refunded_returns_exact_existing_envelope_without_writes(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-refunded.db")
    observation_id = pending_template.refunded_observation_id
    try:
        expected = _reconcile(writer, observation_id, pending_template.caller_kid)
        assert expected["observation_state"] == "refunded"
        _assert_read_is_exact_and_inert(
            writer, observation_id, pending_template.caller_kid, expected
        )
    finally:
        writer.close()


def test_read_blocked_returns_exact_existing_envelope_without_writes(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-blocked.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        _persist_conflicting_refund(writer, observation_id, "event")
        with pytest.raises(ServiceError) as blocked:
            _reconcile(writer, observation_id, pending_template.caller_kid)
        expected = blocked.value.details["observation"]
        assert isinstance(expected, dict)
        assert expected["observation_state"] == "blocked_integrity"
        _assert_read_is_exact_and_inert(
            writer, observation_id, pending_template.caller_kid, expected
        )
    finally:
        writer.close()


def test_read_does_not_adopt_an_already_committed_refund_receipt(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-receipt.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        expected = _reconcile(writer, observation_id, pending_template.caller_kid)
        assert expected["observation_state"] == "refund_pending"
        receipt = _persist_exact_refund(writer, observation_id)
        assert receipt["result"] == {"escrow_status_at_commit": "released"}
        _assert_read_is_exact_and_inert(
            writer, observation_id, pending_template.caller_kid, expected
        )
        row = writer.connection.execute(
            "SELECT state,refund_receipt_event_id,refunded_at_us,result_json "
            "FROM board_legacy_posting_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        assert row is not None
        assert tuple(row) == ("refund_pending", None, None, None)
    finally:
        writer.close()


def test_direct_read_preserves_owner_visibility_without_writes(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-visibility.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        for requested_id, caller_kid, expected_status, expected_error in (
            (observation_id, "foreign-kid", 403, "operation_caller_mismatch"),
            (
                f"obs-{uuid4()}",
                pending_template.caller_kid,
                404,
                "posting_observation_not_found",
            ),
        ):
            before = _database_snapshot(writer.connection)
            with pytest.raises(ServiceError) as raised:
                _get(writer, requested_id, caller_kid)
            assert (raised.value.status_code, raised.value.error) == (
                expected_status,
                expected_error,
            )
            assert _database_snapshot(writer.connection) == before
    finally:
        writer.close()


def test_read_does_not_commit_or_rollback_a_callers_read_transaction(
    pending_template: _PendingTemplate,
    tmp_path: Path,
) -> None:
    writer = _open_copy(pending_template.database, tmp_path / "diagnostic-transaction.db")
    observation_id = pending_template.pending_observation_ids[0]
    try:
        expected = _reconcile(writer, observation_id, pending_template.caller_kid)
        writer.connection.execute("BEGIN")
        assert writer.connection.in_transaction
        _assert_read_is_exact_and_inert(
            writer, observation_id, pending_template.caller_kid, expected
        )
        assert writer.connection.in_transaction
    finally:
        writer.connection.rollback()
        writer.close()
