"""MP-09 H1 additive per-file legacy observation schema contract."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from tests.fakes.mp09_legacy_asset_schema_fixture import (
    ASSET_ID,
    BODY,
    FOREIGN_CONTEXT_ASSET_ID,
    OBSERVATION_COLUMNS,
    OPAQUE_WORKER_ID,
    OTHER_WORKER_ID,
    SECOND_ASSET_ID,
    TASK_ID,
    WORKER_ID,
    apply_h1_schema,
    insert_observation,
    open_h1_source_database,
    preexisting_data_snapshot,
    source_snapshot,
    sqlite_schema_snapshot,
    valid_observation_row,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _open(tmp_path: Path, name: str) -> sqlite3.Connection:
    return open_h1_source_database(tmp_path / name)


def _reject(
    connection: sqlite3.Connection,
    row: dict[str, object],
    *,
    error_prefix: str = "CHECK constraint failed",
) -> None:
    before = tuple(
        tuple(value)
        for value in connection.execute(
            "SELECT * FROM board_legacy_asset_observations ORDER BY observation_id"
        ).fetchall()
    )
    connection.execute("SAVEPOINT rejected_candidate")
    with pytest.raises(sqlite3.IntegrityError) as raised:
        insert_observation(connection, row)
    connection.execute("ROLLBACK TO rejected_candidate")
    connection.execute("RELEASE rejected_candidate")
    assert str(raised.value).startswith(error_prefix)
    after = tuple(
        tuple(value)
        for value in connection.execute(
            "SELECT * FROM board_legacy_asset_observations ORDER BY observation_id"
        ).fetchall()
    )
    assert after == before


def _mutated(**changes: object) -> dict[str, object]:
    row = valid_observation_row()
    row.update(changes)
    return row


def test_v5_fixture_keeps_file_rows_and_task_observation_separate(tmp_path: Path) -> None:
    connection = _open(tmp_path, "v5-control.sqlite3")
    try:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_assets WHERE task_id=?", (TASK_ID,)
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations WHERE task_id=?",
                (TASK_ID,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='board_legacy_asset_observations'"
            ).fetchone()
            is None
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_h1_schema_is_additive_and_preserves_existing_history(tmp_path: Path) -> None:
    connection = _open(tmp_path, "additive.sqlite3")
    try:
        before_schema = sqlite_schema_snapshot(connection)
        before_data = preexisting_data_snapshot(connection)
        apply_h1_schema(connection)
        after_schema = sqlite_schema_snapshot(connection)
        assert preexisting_data_snapshot(connection) == before_data
        after_by_identity = {(row[0], row[1]): row for row in after_schema}
        assert all(after_by_identity[(row[0], row[1])] == row for row in before_schema)
        delta = tuple(row for row in after_schema if row not in before_schema)
        assert tuple(row[1] for row in delta if row[0] == "table") == (
            "board_legacy_asset_observations",
        )
        assert any(row[0] == "index" for row in delta)
        assert all(
            row[2] == "board_legacy_asset_observations" and row[0] in {"table", "index"}
            for row in delta
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations WHERE task_id=?",
                (TASK_ID,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM board_legacy_asset_observations").fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_h1_schema_has_exact_19_columns_and_required_indexes(tmp_path: Path) -> None:
    connection = _open(tmp_path, "shape.sqlite3")
    try:
        apply_h1_schema(connection)
        columns = connection.execute(
            "PRAGMA table_info(board_legacy_asset_observations)"
        ).fetchall()
        nullable = {
            "assigned_worker_id",
            "observed_size_bytes",
            "observed_sha256",
            "generated_artifact_id",
            "generated_object_key",
        }
        integer_columns = {
            "observation_contract_version",
            "observed_at_us",
            "observed_size_bytes",
        }
        assert tuple(
            (str(row[1]), str(row[2]), int(row[3]), int(row[5])) for row in columns
        ) == tuple(
            (
                name,
                "INTEGER" if name in integer_columns else "TEXT",
                0 if name in nullable else 1,
                1 if name == "observation_id" else 0,
            )
            for name in OBSERVATION_COLUMNS
        )
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(board_legacy_asset_observations)"
        ).fetchall()
        assert {(row[2], row[3], row[4], row[5], row[6], row[7]) for row in foreign_keys} == {
            ("board_assets", "legacy_asset_id", "asset_id", "RESTRICT", "RESTRICT", "NONE"),
            ("board_tasks", "task_id", "task_id", "RESTRICT", "RESTRICT", "NONE"),
            (
                "identity_agents",
                "uploader_id",
                "agent_id",
                "RESTRICT",
                "RESTRICT",
                "NONE",
            ),
            (
                "identity_agents",
                "assigned_worker_id",
                "agent_id",
                "RESTRICT",
                "RESTRICT",
                "NONE",
            ),
        }
        indexes = connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='index' "
            "AND tbl_name='board_legacy_asset_observations' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        assert indexes and all(row[1] is not None for row in indexes)
    finally:
        connection.close()


def test_valid_verified_observations_allow_multiple_assets_for_one_task(tmp_path: Path) -> None:
    connection = _open(tmp_path, "valid.sqlite3")
    try:
        apply_h1_schema(connection)
        first = valid_observation_row()
        second = valid_observation_row(asset_id=SECOND_ASSET_ID)
        insert_observation(connection, first)
        insert_observation(connection, second)
        connection.commit()
        rows = connection.execute(
            "SELECT legacy_asset_id,task_id,copy_status FROM "
            "board_legacy_asset_observations ORDER BY legacy_asset_id"
        ).fetchall()
        assert tuple(tuple(row) for row in rows) == (
            (ASSET_ID, TASK_ID, "installed"),
            (SECOND_ASSET_ID, TASK_ID, "installed"),
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations WHERE task_id=?",
                (TASK_ID,),
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observation_id", None),
        ("observation_contract_version", None),
        ("migration_plan_id", None),
        ("legacy_asset_id", None),
        ("task_id", None),
        ("uploader_id", None),
        ("source_row_sha256", None),
        ("attribution_status", None),
        ("byte_status", None),
        ("declared_hash_status", None),
        ("copy_status", None),
        ("canonical_observation_json", None),
        ("observation_sha256", None),
        ("observed_at_us", None),
    ],
)
def test_required_columns_reject_null(field: str, value: object, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"null-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(
            connection,
            _mutated(**{field: value}),
            error_prefix="NOT NULL constraint failed",
        )
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


def test_exact_five_nullable_columns_accept_null_together(tmp_path: Path) -> None:
    connection = _open(tmp_path, "five-nullable.sqlite3")
    try:
        apply_h1_schema(connection)
        row = _mutated(
            assigned_worker_id=None,
            attribution_status="worker_unassigned",
            byte_status="missing",
            declared_hash_status="absent",
            copy_status="unavailable",
            observed_size_bytes=None,
            observed_sha256=None,
            generated_artifact_id=None,
            generated_object_key=None,
        )
        insert_observation(connection, row)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field",
    [
        "observation_id",
        "migration_plan_id",
        "legacy_asset_id",
        "task_id",
        "uploader_id",
        "source_row_sha256",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "canonical_observation_json",
        "observation_sha256",
    ],
)
def test_required_text_columns_reject_empty_bytes(field: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"empty-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: ""}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field",
    [
        "observation_id",
        "migration_plan_id",
        "legacy_asset_id",
        "task_id",
        "uploader_id",
        "assigned_worker_id",
        "source_row_sha256",
        "attribution_status",
        "byte_status",
        "declared_hash_status",
        "copy_status",
        "canonical_observation_json",
        "observation_sha256",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
    ],
)
def test_text_columns_reject_blob_storage(field: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"blob-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: sqlite3.Binary(b"x")}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field",
    [
        "assigned_worker_id",
        "observed_sha256",
        "generated_artifact_id",
        "generated_object_key",
    ],
)
def test_nullable_text_columns_reject_empty_when_present(field: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"nullable-empty-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: ""}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observation_contract_version", 0),
        ("observation_contract_version", 2),
        ("observation_contract_version", 1.5),
        ("observed_at_us", -1),
        ("observed_at_us", 9_223_372_036_854_775_808.0),
        ("observed_at_us", 1.5),
        ("observed_size_bytes", -1),
        ("observed_size_bytes", 9_223_372_036_854_775_808.0),
        ("observed_size_bytes", 1.5),
    ],
)
def test_integer_storage_and_ranges_are_exact(field: str, value: object, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"integer-{field}-{value}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: value}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_sqlite_integer_affinity_control_accepts_integral_values(
    value: object, tmp_path: Path
) -> None:
    connection = _open(tmp_path, f"affinity-{type(value).__name__}.sqlite3")
    try:
        apply_h1_schema(connection)
        row = _mutated(observation_contract_version=value)
        insert_observation(connection, row)
        stored = connection.execute(
            "SELECT observation_contract_version,typeof(observation_contract_version) "
            "FROM board_legacy_asset_observations"
        ).fetchone()
        assert stored is not None and tuple(stored) == (1, "integer")
    finally:
        connection.close()


@pytest.mark.parametrize("value", [0, 9_223_372_036_854_775_807])
@pytest.mark.parametrize("field", ["observed_at_us", "observed_size_bytes"])
def test_integer_bounds_are_accepted(field: str, value: int, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"integer-bound-{field}-{value}.sqlite3")
    try:
        apply_h1_schema(connection)
        insert_observation(connection, _mutated(**{field: value}))
        stored = connection.execute(
            f"SELECT {field},typeof({field}) FROM board_legacy_asset_observations"  # nosec B608
        ).fetchone()
        assert stored is not None and tuple(stored) == (value, "integer")
    finally:
        connection.close()


@pytest.mark.parametrize("field", ["migration_plan_id", "source_row_sha256", "observation_sha256"])
@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ("a" * 63) + "\x00"])
def test_required_hashes_are_exact_lowercase_hex(field: str, value: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"hash-{field}-{len(value)}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: value}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, ("a" * 63) + "\x00"])
def test_nullable_observed_hash_is_null_or_lowercase_hex(value: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"observed-hash-{len(value)}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(observed_sha256=value))
        unavailable = _mutated(
            byte_status="missing",
            declared_hash_status="absent",
            copy_status="unavailable",
            observed_size_bytes=None,
            observed_sha256=None,
            generated_artifact_id=None,
            generated_object_key=None,
        )
        insert_observation(connection, unavailable)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attribution_status", "unknown"),
        ("byte_status", "unknown"),
        ("declared_hash_status", "unknown"),
        ("copy_status", "unknown"),
    ],
)
def test_status_columns_reject_unknown_values(field: str, value: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"enum-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**{field: value}))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize("value", ["not-json", "[]", "null", "1"])
def test_canonical_observation_requires_a_json_object(value: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"json-{value[:3]}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(canonical_observation_json=value))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("byte_status", "declared_hash_status", "has_observed_pair"),
    [
        ("unsafe_path", "absent", False),
        ("unsafe_path", "unobserved", False),
        ("shared_source_conflict", "absent", False),
        ("shared_source_conflict", "unobserved", False),
        ("missing", "absent", False),
        ("missing", "unobserved", False),
        ("symlink", "absent", False),
        ("symlink", "unobserved", False),
        ("non_regular", "absent", False),
        ("non_regular", "unobserved", False),
        ("verified", "absent", True),
        ("verified", "matched", True),
        ("size_mismatch", "absent", True),
        ("size_mismatch", "matched", True),
        ("size_mismatch", "mismatch", True),
        ("hash_mismatch", "mismatch", True),
    ],
)
def test_legal_status_pair_matrix_is_accepted(
    byte_status: str,
    declared_hash_status: str,
    has_observed_pair: bool,
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, f"legal-pair-{byte_status}-{declared_hash_status}.sqlite3")
    try:
        apply_h1_schema(connection)
        row = _mutated(
            byte_status=byte_status,
            declared_hash_status=declared_hash_status,
            copy_status="installed" if byte_status == "verified" else "unavailable",
            observed_size_bytes=len(BODY) if has_observed_pair else None,
            observed_sha256="e" * 64 if has_observed_pair else None,
            generated_artifact_id=(
                valid_observation_row()["generated_artifact_id"]
                if byte_status == "verified"
                else None
            ),
            generated_object_key=(
                valid_observation_row()["generated_object_key"]
                if byte_status == "verified"
                else None
            ),
        )
        insert_observation(connection, row)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("byte_status", "declared_hash_status", "has_observed_pair"),
    [
        ("verified", "unobserved", True),
        ("verified", "mismatch", True),
        ("size_mismatch", "unobserved", True),
        ("hash_mismatch", "absent", True),
        ("hash_mismatch", "unobserved", False),
        ("hash_mismatch", "matched", True),
        ("unsafe_path", "matched", False),
        ("unsafe_path", "mismatch", False),
        ("shared_source_conflict", "matched", False),
        ("shared_source_conflict", "mismatch", False),
        ("missing", "matched", False),
        ("missing", "mismatch", False),
        ("symlink", "matched", False),
        ("symlink", "mismatch", False),
        ("non_regular", "matched", False),
        ("non_regular", "mismatch", False),
    ],
)
def test_illegal_status_pair_matrix_is_rejected(
    byte_status: str,
    declared_hash_status: str,
    has_observed_pair: bool,
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, f"illegal-pair-{byte_status}-{declared_hash_status}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(
            connection,
            _mutated(
                byte_status=byte_status,
                declared_hash_status=declared_hash_status,
                copy_status="installed" if byte_status == "verified" else "unavailable",
                observed_size_bytes=len(BODY) if has_observed_pair else None,
                observed_sha256="e" * 64 if has_observed_pair else None,
                generated_artifact_id=(
                    valid_observation_row()["generated_artifact_id"]
                    if byte_status == "verified"
                    else None
                ),
                generated_object_key=(
                    valid_observation_row()["generated_object_key"]
                    if byte_status == "verified"
                    else None
                ),
            ),
        )
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "byte_status",
    [
        "unsafe_path",
        "shared_source_conflict",
        "missing",
        "symlink",
        "non_regular",
        "size_mismatch",
        "hash_mismatch",
    ],
)
def test_installed_rejects_every_nonverified_byte_status(
    byte_status: str,
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, f"installed-nonverified-{byte_status}.sqlite3")
    try:
        apply_h1_schema(connection)
        no_read = byte_status in {
            "unsafe_path",
            "shared_source_conflict",
            "missing",
            "symlink",
            "non_regular",
        }
        _reject(
            connection,
            _mutated(
                byte_status=byte_status,
                declared_hash_status="mismatch" if byte_status == "hash_mismatch" else "absent",
                observed_size_bytes=None if no_read else len(BODY),
                observed_sha256=None if no_read else "e" * 64,
            ),
        )
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_size_bytes": None},
        {"observed_sha256": None},
        {"generated_artifact_id": None},
        {"generated_object_key": None},
        {"copy_status": "unavailable"},
        {
            "copy_status": "unavailable",
            "generated_artifact_id": None,
            "generated_object_key": None,
        },
    ],
)
def test_verified_installed_pairs_are_biconditional(
    changes: dict[str, object], tmp_path: Path
) -> None:
    connection = _open(tmp_path, "verified-installed-pairs.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(connection, _mutated(**changes))
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("attribution_status", "assigned_worker_id"),
    [
        ("verified_worker", None),
        ("verified_worker", OTHER_WORKER_ID),
        ("worker_unassigned", WORKER_ID),
        ("uploader_mismatch", None),
        ("uploader_mismatch", WORKER_ID),
    ],
)
def test_attribution_status_and_worker_projection_agree(
    attribution_status: str,
    assigned_worker_id: str | None,
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, f"attribution-{attribution_status}.sqlite3")
    try:
        apply_h1_schema(connection)
        _reject(
            connection,
            _mutated(
                attribution_status=attribution_status,
                assigned_worker_id=assigned_worker_id,
            ),
        )
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


def test_worker_unassigned_and_uploader_mismatch_can_preserve_verified_copies(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, "attribution-controls.sqlite3")
    try:
        apply_h1_schema(connection)
        unassigned = _mutated(
            attribution_status="worker_unassigned",
            assigned_worker_id=None,
        )
        insert_observation(connection, unassigned)
        mismatch = valid_observation_row(asset_id=SECOND_ASSET_ID)
        mismatch["attribution_status"] = "uploader_mismatch"
        mismatch["assigned_worker_id"] = OTHER_WORKER_ID
        insert_observation(connection, mismatch)
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_opaque_nul_text_is_accepted_when_fk_reference_exists(tmp_path: Path) -> None:
    connection = _open(tmp_path, "opaque-nul.sqlite3")
    try:
        apply_h1_schema(connection)
        row = _mutated(
            attribution_status="uploader_mismatch",
            assigned_worker_id=OPAQUE_WORKER_ID,
        )
        insert_observation(connection, row)
        stored = connection.execute(
            "SELECT assigned_worker_id,typeof(assigned_worker_id),"
            "length(CAST(assigned_worker_id AS BLOB)) "
            "FROM board_legacy_asset_observations"
        ).fetchone()
        assert stored is not None
        assert tuple(stored) == (
            OPAQUE_WORKER_ID,
            "text",
            len(OPAQUE_WORKER_ID.encode("utf-8")),
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("legacy_asset_id", "missing-asset"),
        ("task_id", "missing-task"),
        ("uploader_id", "missing-uploader"),
        ("assigned_worker_id", "missing-worker"),
    ],
)
def test_foreign_keys_reject_missing_references(field: str, value: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"foreign-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        changes: dict[str, object] = {field: value}
        if field == "uploader_id":
            changes.update(
                attribution_status="uploader_mismatch",
                assigned_worker_id=OTHER_WORKER_ID,
            )
        if field == "assigned_worker_id":
            changes["attribution_status"] = "uploader_mismatch"
        _reject(
            connection,
            _mutated(**changes),
            error_prefix="FOREIGN KEY constraint failed",
        )
        insert_observation(connection, valid_observation_row())
    finally:
        connection.close()


@pytest.mark.parametrize(
    "field",
    [
        "observation_id",
        "legacy_asset_id",
        "observation_sha256",
        "generated_artifact_id",
        "generated_object_key",
    ],
)
def test_unique_observation_and_generated_identities(field: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"unique-{field}.sqlite3")
    try:
        apply_h1_schema(connection)
        first = valid_observation_row()
        second = valid_observation_row(asset_id=SECOND_ASSET_ID)
        second[field] = first[field]
        insert_observation(connection, first)
        _reject(connection, second, error_prefix="UNIQUE constraint failed")
        assert (
            connection.execute("SELECT COUNT(*) FROM board_legacy_asset_observations").fetchone()[0]
            == 1
        )
        insert_observation(connection, valid_observation_row(asset_id=SECOND_ASSET_ID))
    finally:
        connection.close()


@pytest.mark.parametrize("source_table", ["board_assets", "board_tasks", "identity_agents"])
def test_source_references_are_delete_restricted(source_table: str, tmp_path: Path) -> None:
    connection = _open(tmp_path, f"restrict-{source_table}.sqlite3")
    try:
        apply_h1_schema(connection)
        insert_observation(connection, valid_observation_row())
        connection.commit()
        before = source_snapshot(connection)
        statements = {
            "board_assets": ("DELETE FROM board_assets WHERE asset_id=?", (ASSET_ID,)),
            "board_tasks": ("DELETE FROM board_tasks WHERE task_id=?", (TASK_ID,)),
            "identity_agents": ("DELETE FROM identity_agents WHERE agent_id=?", (WORKER_ID,)),
        }
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(*statements[source_table])
        connection.rollback()
        assert source_snapshot(connection) == before
    finally:
        connection.close()


def test_sql_does_not_claim_parser_owned_hash_uuid_or_canonical_validation(tmp_path: Path) -> None:
    connection = _open(tmp_path, "parser-owned.sqlite3")
    try:
        apply_h1_schema(connection)
        row = valid_observation_row()
        row["observation_id"] = "opaque-but-nonempty"
        row["canonical_observation_json"] = json.dumps({"structurally": "valid"})
        row["observation_sha256"] = "d" * 64
        insert_observation(connection, row)
        assert (
            connection.execute("SELECT COUNT(*) FROM board_legacy_asset_observations").fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_individual_foreign_keys_do_not_claim_source_context_authority(
    tmp_path: Path,
) -> None:
    connection = _open(tmp_path, "parser-context-owned.sqlite3")
    try:
        apply_h1_schema(connection)
        row = valid_observation_row(asset_id=FOREIGN_CONTEXT_ASSET_ID)
        assert row["task_id"] == TASK_ID
        assert row["uploader_id"] == WORKER_ID
        source_context = connection.execute(
            "SELECT task_id,uploader_id FROM board_assets WHERE asset_id=?",
            (FOREIGN_CONTEXT_ASSET_ID,),
        ).fetchone()
        assert source_context is not None
        assert tuple(source_context) != (row["task_id"], row["uploader_id"])
        insert_observation(connection, row)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
