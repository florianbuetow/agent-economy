"""T-124 exact V5 schema contract for durable financial-effect delivery."""

from __future__ import annotations

import sqlite3
from typing import Any, cast

import pytest

from db_gateway_service.config import Settings
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

pytestmark = pytest.mark.unit

_EMPTY_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
_MAX_TIME_US = 253402300799999999


def test_gateway_requires_explicit_board_effect_recovery_configuration() -> None:
    field = Settings.model_fields.get("board_effect_recovery")
    assert field is not None, "Missing explicit board_effect_recovery configuration capability"
    model = field.annotation
    model_fields = getattr(model, "model_fields", None)
    assert isinstance(model_fields, dict)
    assert set(model_fields) == {
        "current_task_board_caller_kid",
        "max_attempts_per_delivery",
        "initial_backoff_seconds",
        "max_backoff_seconds",
        "lease_duration_seconds",
        "evidence_interval_seconds",
        "gateway_timeout_seconds",
        "bank_timeout_seconds",
    }
    assert all(item.is_required() for item in model_fields.values())


def _recovery_config_model() -> Any:
    field = Settings.model_fields.get("board_effect_recovery")
    assert field is not None, "Missing explicit board_effect_recovery configuration capability"
    return cast("Any", field.annotation)


def _valid_recovery_config() -> dict[str, object]:
    return {
        "current_task_board_caller_kid": "gateway-task-board",
        "max_attempts_per_delivery": 8,
        "initial_backoff_seconds": 2,
        "max_backoff_seconds": 60,
        "lease_duration_seconds": 45,
        "evidence_interval_seconds": 60,
        "gateway_timeout_seconds": 10,
        "bank_timeout_seconds": 10,
    }


def test_gateway_recovery_configuration_accepts_exact_operational_values() -> None:
    configured = _recovery_config_model()(**_valid_recovery_config())
    assert configured.model_dump() == _valid_recovery_config()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("current_task_board_caller_kid", ""),
        ("current_task_board_caller_kid", 1),
        ("max_attempts_per_delivery", 0),
        ("max_attempts_per_delivery", True),
        ("max_attempts_per_delivery", "8"),
        ("max_attempts_per_delivery", 8.0),
        ("initial_backoff_seconds", 0),
        ("initial_backoff_seconds", True),
        ("initial_backoff_seconds", "2"),
        ("initial_backoff_seconds", 2.5),
        ("max_backoff_seconds", 1),
        ("max_backoff_seconds", True),
        ("max_backoff_seconds", "60"),
        ("max_backoff_seconds", 60.5),
        ("lease_duration_seconds", 40),
        ("lease_duration_seconds", True),
        ("lease_duration_seconds", "45"),
        ("lease_duration_seconds", 45.5),
        ("evidence_interval_seconds", 0),
        ("evidence_interval_seconds", True),
        ("evidence_interval_seconds", "60"),
        ("evidence_interval_seconds", 60.5),
        ("gateway_timeout_seconds", 0),
        ("gateway_timeout_seconds", True),
        ("gateway_timeout_seconds", "10"),
        ("gateway_timeout_seconds", 10.5),
        ("bank_timeout_seconds", 0),
        ("bank_timeout_seconds", True),
        ("bank_timeout_seconds", "10"),
        ("bank_timeout_seconds", 10.5),
    ],
)
def test_gateway_recovery_configuration_rejects_invalid_values(
    field_name: str,
    invalid_value: object,
) -> None:
    values = _valid_recovery_config()
    values[field_name] = invalid_value
    with pytest.raises(ValueError):
        _recovery_config_model()(**values)


@pytest.mark.parametrize("missing", sorted(_valid_recovery_config()))
def test_gateway_recovery_configuration_rejects_every_missing_field(missing: str) -> None:
    values = _valid_recovery_config()
    del values[missing]
    with pytest.raises(ValueError):
        _recovery_config_model()(**values)


def test_gateway_recovery_configuration_rejects_extra_field() -> None:
    values = _valid_recovery_config()
    values["implicit_default"] = 1
    with pytest.raises(ValueError):
        _recovery_config_model()(**values)


def _v5_state() -> SchemaState:
    state = getattr(SchemaState, "CURRENT_V5", None)
    assert state is not None, "Missing explicit CURRENT_V5 schema capability"
    return state


def _fresh_v5() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(load_schema_sql(_v5_state()))
    return connection


def _insert_delivery(
    connection: sqlite3.Connection,
    **overrides: object,
) -> None:
    values: dict[str, object] = {
        "effect_operation_id": "op-00000000-0000-4000-8000-000000000001",
        "recovery_contract_version": 1,
        "task_id": "task-1",
        "source_kind": "native_task_operation",
        "native_operation_id": "op-00000000-0000-4000-8000-000000000001",
        "legacy_observation_id": None,
        "owner_caller_kid": "gateway-task-board",
        "effect_kind": "bank_release",
        "delivery_state": "pending",
        "enqueued_at_us": 1,
        "total_attempt_count": 0,
        "cycle_attempt_count": 0,
        "lease_generation": 0,
        "next_attempt_at_us": 1,
        "lease_owner_id": None,
        "lease_acquired_at_us": None,
        "lease_expires_at_us": None,
        "last_failure_code": None,
        "last_failure_at_us": None,
        "operator_retry_count": 0,
        "last_operator_retry_at_us": None,
        "last_operator_retry_caller_kid": None,
        "next_evidence_check_at_us": None,
        "completed_at_us": None,
        "completion_event_id": None,
        "cutover_singleton": None,
    }
    values.update(overrides)
    columns = ",".join(values)
    placeholders = ",".join("?" for _ in values)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        f"INSERT INTO board_task_effect_deliveries ({columns}) "  # nosec B608
        f"VALUES ({placeholders})",  # nosec B608
        tuple(values.values()),
    )


def test_fresh_v5_has_exact_cutover_attestation_and_no_deliveries() -> None:
    connection = _fresh_v5()
    try:
        assert classify_schema(connection).state is _v5_state()
        assert connection.execute(
            "SELECT singleton, cutover_version, cutover_kind, source_schema_version, "
            "source_snapshot_sha256, migration_plan_sha256, backup_id, "
            "backup_manifest_sha256, caller_kid, native_member_count, "
            "native_members_sha256, legacy_member_count, legacy_members_sha256, "
            "combined_member_count, combined_members_sha256 "
            "FROM board_task_effect_delivery_cutover"
        ).fetchall() == [
            (
                1,
                1,
                "fresh",
                None,
                None,
                None,
                None,
                None,
                None,
                0,
                _EMPTY_HASH,
                0,
                _EMPTY_HASH,
                0,
                _EMPTY_HASH,
            )
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM board_task_effect_deliveries"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_v5_catalog_has_exact_delivery_indexes_and_foreign_keys() -> None:
    connection = _fresh_v5()
    try:
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='board_task_effect_deliveries'"
            )
        }
        assert {
            "idx_board_effect_delivery_pending",
            "idx_board_effect_delivery_leased",
            "idx_board_effect_delivery_dead_letter_evidence",
            "idx_board_effect_delivery_diagnostic",
        } <= indexes
        foreign_keys = {
            (row[3], row[2], row[4])
            for row in connection.execute("PRAGMA foreign_key_list(board_task_effect_deliveries)")
        }
        assert {
            ("task_id", "board_tasks", "task_id"),
            ("native_operation_id", "board_task_operations", "operation_id"),
            ("legacy_observation_id", "board_legacy_effect_observations", "observation_id"),
            ("completion_event_id", "events", "event_id"),
            ("cutover_singleton", "board_task_effect_delivery_cutover", "singleton"),
        } == foreign_keys
    finally:
        connection.close()


def test_v5_delivery_sql_keeps_exact_state_and_temporal_guards() -> None:
    connection = _fresh_v5()
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='board_task_effect_deliveries'"
        ).fetchone()
        assert row is not None
        sql = " ".join(str(row[0]).split())
        required = (
            "native_operation_id IS NOT NULL",
            "native_operation_id = effect_operation_id",
            "cutover_singleton IS NOT NULL",
            "effect_kind = 'bank_release'",
            "UNIQUE (task_id)",
            "lease_expires_at_us > lease_acquired_at_us",
            "completed_at_us BETWEEN enqueued_at_us AND 253402300799999999",
            "last_failure_code IS NULL AND last_failure_at_us IS NULL",
            "delivery_state = 'integrity_blocked'",
            "last_failure_code = 'effect_evidence_conflict'",
        )
        assert all(fragment in sql for fragment in required)
    finally:
        connection.close()


@pytest.mark.parametrize("bad_time", [-1, _MAX_TIME_US + 1, 1.5])
def test_v5_rejects_out_of_range_or_noninteger_enqueue_time(bad_time: object) -> None:
    connection = _fresh_v5()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO board_task_effect_deliveries (effect_operation_id, "
                "recovery_contract_version, task_id, source_kind, native_operation_id, "
                "owner_caller_kid, effect_kind, delivery_state, enqueued_at_us, "
                "total_attempt_count, cycle_attempt_count, lease_generation, "
                "next_attempt_at_us, operator_retry_count, cutover_singleton) VALUES "
                "('op-00000000-0000-4000-8000-000000000001', 1, 'missing-task', "
                "'native_task_operation', 'op-00000000-0000-4000-8000-000000000001', "
                "'gateway-task-board', 'bank_release', 'pending', ?, 0, 0, 0, 0, 0, NULL)",
                (bad_time,),
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"effect_operation_id": "not-canonical"},
        {"native_operation_id": None},
        {"native_operation_id": "op-00000000-0000-4000-8000-000000000002"},
        {
            "source_kind": "legacy_settlement_observation",
            "native_operation_id": None,
            "legacy_observation_id": "obs-00000000-0000-4000-8000-000000000001",
            "cutover_singleton": None,
        },
        {"last_failure_code": "request_timeout", "last_failure_at_us": None},
        {"operator_retry_count": 1},
        {
            "delivery_state": "leased",
            "next_attempt_at_us": None,
            "lease_owner_id": "recovery-00000000-0000-4000-8000-000000000001",
            "lease_acquired_at_us": 5,
            "lease_expires_at_us": 5,
        },
        {
            "delivery_state": "completed",
            "next_attempt_at_us": None,
            "completed_at_us": 2,
            "completion_event_id": None,
        },
        {
            "delivery_state": "integrity_blocked",
            "next_attempt_at_us": None,
            "last_failure_code": "request_timeout",
            "last_failure_at_us": 2,
        },
    ],
)
def test_v5_rejects_invalid_source_state_and_pairing_combinations(
    overrides: dict[str, object],
) -> None:
    connection = _fresh_v5()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_delivery(connection, **overrides)
    finally:
        connection.close()
