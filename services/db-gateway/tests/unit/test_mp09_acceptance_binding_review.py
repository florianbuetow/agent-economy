"""Review regressions for native V6 acceptance failure boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_acceptance_binding_fixture import (
    ACCEPT_OPERATION_ID,
    claim_acceptance,
    full_snapshot,
    prepare_native_acceptance,
)
from tests.fakes.mp09_acceptance_review_fixture import (
    AcceptanceRuntimeTarget,
    event_identity_writer,
    numeric_string_event_identity_writer,
    runtime_failure_writer,
)
from tests.fakes.mp09_receipt_adoption_fixture import TASK_ID, bank_snapshot

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes.mp09_acceptance_binding_fixture import AcceptanceBindingFixture

pytestmark = pytest.mark.unit

_WRITE_TARGETS: tuple[AcceptanceRuntimeTarget, ...] = (
    "task_update",
    "timing_update",
    "artifact_update",
    "event_insert",
    "operation_insert",
)


def _assert_exact_persistence_failure(
    fixture: AcceptanceBindingFixture,
    call: Callable[[], object],
) -> None:
    before = full_snapshot(fixture.connection)
    bank_before = bank_snapshot(fixture.connection)
    observed: Exception | None = None
    try:
        call()
    except Exception as exc:
        observed = exc
    assert bank_snapshot(fixture.connection) == bank_before
    assert full_snapshot(fixture.connection) == before
    assert isinstance(observed, ServiceError)
    assert (observed.status_code, observed.error) == (
        500,
        "task_operation_persistence_failed",
    )


def _assert_artifact_contract_invalid(
    fixture: AcceptanceBindingFixture,
) -> None:
    before = full_snapshot(fixture.connection)
    bank_before = bank_snapshot(fixture.connection)
    observed: Exception | None = None
    try:
        claim_acceptance(fixture)
    except Exception as exc:
        observed = exc
    assert bank_snapshot(fixture.connection) == bank_before
    assert full_snapshot(fixture.connection) == before
    assert isinstance(observed, ServiceError)
    assert (observed.status_code, observed.error) == (
        500,
        "artifact_contract_invalid",
    )


@pytest.mark.parametrize("target", _WRITE_TARGETS)
def test_runtime_error_at_each_acceptance_write_is_uniform_and_atomic(
    tmp_path: Path,
    target: AcceptanceRuntimeTarget,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"runtime-{target}.sqlite3")
    try:
        writer = runtime_failure_writer(fixture, target=target)
        _assert_exact_persistence_failure(
            fixture,
            lambda: claim_acceptance(fixture, writer=writer),
        )
    finally:
        fixture.close()


@pytest.mark.parametrize(
    "identity_kind",
    [
        "none",
        "non_integer",
        "numeric_string_actual",
        "zero",
        "negative",
        "stale_positive",
    ],
)
def test_invalid_or_stale_event_identity_is_uniform_and_atomic(
    tmp_path: Path,
    identity_kind: str,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"event-id-{identity_kind}.sqlite3")
    try:
        if identity_kind == "numeric_string_actual":
            writer = numeric_string_event_identity_writer(fixture)
        elif identity_kind == "none":
            lastrowid: object = None
        elif identity_kind == "non_integer":
            lastrowid = "not-an-integer"
        elif identity_kind == "zero":
            lastrowid = 0
        elif identity_kind == "negative":
            lastrowid = -1
        else:
            existing = fixture.connection.execute(
                "SELECT event_id FROM events WHERE task_id IS NULL ORDER BY event_id LIMIT 1"
            ).fetchone()
            assert existing is not None and type(existing[0]) is int and int(existing[0]) > 0
            lastrowid = int(existing[0])
        if identity_kind != "numeric_string_actual":
            writer = event_identity_writer(fixture, lastrowid=lastrowid)
        _assert_exact_persistence_failure(
            fixture,
            lambda: claim_acceptance(fixture, writer=writer),
        )
    finally:
        fixture.close()


def test_after_insert_deleted_operation_returns_uniform_failure_and_rolls_back(
    tmp_path: Path,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / "deleted-operation.sqlite3")
    try:
        fixture.connection.execute(
            "CREATE TRIGGER mp09_delete_acceptance_operation "
            "AFTER INSERT ON board_task_operations "
            "WHEN NEW.operation_id='" + ACCEPT_OPERATION_ID + "' "
            "BEGIN DELETE FROM board_task_operations "
            "WHERE operation_id=NEW.operation_id; END"
        )
        fixture.connection.commit()
        _assert_exact_persistence_failure(
            fixture,
            lambda: claim_acceptance(fixture),
        )
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("input_provenance", "unknown_pre_v6"),
        ("artifact_contract_version", "not-an-integer"),
    ],
)
def test_controlled_native_contract_corruption_maps_to_artifact_contract_invalid(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / f"contract-{column}.sqlite3")
    try:
        fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
        if column == "input_provenance":
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET input_provenance=? WHERE task_id=?",
                (value, TASK_ID),
            )
        else:
            assert column == "artifact_contract_version"
            fixture.connection.execute(
                "UPDATE board_task_artifact_contracts SET artifact_contract_version=? "
                "WHERE task_id=?",
                (value, TASK_ID),
            )
        fixture.connection.commit()
        fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        restored = fixture.connection.execute("PRAGMA ignore_check_constraints").fetchone()
        assert restored is not None and tuple(restored) == (0,)
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        _assert_artifact_contract_invalid(fixture)
    finally:
        fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
        fixture.close()
