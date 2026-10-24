"""Regression proofs for strict MP-05 publication audit provenance and JSON types."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.migration import MigrationError, migrate_v3_to_v4, migrate_v4_to_v5
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.schema import SchemaState, classify_schema
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_publication_requests import parse_task_publication_request
from tests.fakes.mp05_publication_history import publication_intent
from tests.fakes.mp05_publication_migration_history import (
    V3Source,
    add_rich_cutover_history,
    create_funded_payer,
    open_v3_writer,
    prepare_v3_source,
    prepare_v3_source_with_legacy_refund,
    publish_verified_backup,
    verify_backup_without_mutating,
)
from tests.fakes.mp05_shared_legacy_history import (
    SHARED_LEGACY_ESCROWS,
    SHARED_LEGACY_TASK_ID,
    prepare_v3_source_with_shared_legacy_locks,
)
from tests.unit import test_mp06_migration as old_migration

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class _FactCase:
    fixture: str
    selector: str
    path: tuple[str, ...]
    replacement: str

    @property
    def id(self) -> str:
        return f"{self.fixture}-{self.selector}-{'-'.join(self.path)}-{self.replacement}"


_FACT_CASES = (
    tuple(
        _FactCase("native", selector, path, replacement)
        for selector, paths in (
            (
                "pending",
                (
                    ("observation_version",),
                    ("escrow", "amount"),
                    ("escrow", "event_id"),
                    ("lock_provenance", "event_id"),
                ),
            ),
            (
                "refunded",
                (("existing_refund_provenance", "event_id"),),
            ),
        )
        for path in paths
        for replacement in ("bool", "float")
    )
    + tuple(
        _FactCase("legacy", "refunded", path, replacement)
        for path in (
            ("observation_version",),
            ("escrow", "amount"),
            ("escrow", "event_id"),
            ("lock_provenance", "cutover_singleton"),
            ("existing_refund_provenance", "cutover_singleton"),
        )
        for replacement in ("bool", "float")
    )
    + (
        _FactCase("native", "pending", ("escrow", "status"), "released"),
        _FactCase(
            "native",
            "pending",
            ("escrow", "resolved_at"),
            "2026-10-17T12:00:00.000000Z",
        ),
        _FactCase("native", "refunded", ("escrow", "status"), "locked"),
        _FactCase("legacy", "refunded", ("escrow", "status"), "locked"),
    )
)

_PUBLISHED_INTEGER_FIELDS = (
    "reward",
    "bidding_deadline_seconds",
    "acceptance_window_seconds",
    "execution_deadline_seconds",
    "review_deadline_seconds",
    "bidding_cutoff_us",
    "acceptance_cutoff_us",
    "lifecycle_version",
    "timing_contract_version",
    "event_id",
)


@pytest.fixture(scope="module")
def native_v4_template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("mp05-native-audit")
    source = _migrate_native_source(root)
    operation_id = _publish_native_task(source)
    _assert_standalone(source.database)
    return source.database, operation_id


@pytest.fixture(scope="module")
def legacy_v4_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("mp05-legacy-audit")
    source = _migrate_legacy_source(root)
    _assert_standalone(source.database)
    return source.database


def test_two_legacy_payers_for_one_task_remain_independently_auditable(
    tmp_path: Path,
) -> None:
    source = prepare_v3_source_with_shared_legacy_locks(tmp_path)
    backup_id = "shared-before-mp05"
    verification = publish_verified_backup(source, backup_id=backup_id)
    backup_digest = old_migration._tree_digest(source.backup_root / backup_id)
    backup_database = source.backup_root / backup_id / "payload/database/economy.db"
    expected = _legacy_transactions_by_payer(backup_database)
    assert set(expected) == {"a-external", "a-worker"}
    assert all(len(value) == 1 for value in expected.values())

    try:
        migrated = migrate_v3_to_v4(
            source.inventory,
            backup_root=source.backup_root,
            backup_id=backup_id,
            work_root=source.work_root,
            busy_timeout_ms=5000,
        )
    except MigrationError as exc:
        assert old_migration._tree_digest(source.backup_root / backup_id) == backup_digest
        assert verify_backup_without_mutating(source, backup_id=backup_id) == verification
        unchanged = _open_immutable(source.database)
        try:
            assert classify_schema(unchanged).state is SchemaState.CURRENT_V3
        finally:
            unchanged.close()
        pytest.fail(f"Valid shared-task legacy locks were rejected: {exc.reason}")
    assert migrated.observation == "committed_v4_verified"
    assert old_migration._tree_digest(source.backup_root / backup_id) == backup_digest
    assert verify_backup_without_mutating(source, backup_id=backup_id) == verification

    connection = _open_immutable(source.database)
    try:
        observations = connection.execute(
            "SELECT escrow_id,poster_id,observed_fact_json "
            "FROM board_legacy_posting_observations WHERE task_id=? ORDER BY escrow_id",
            (SHARED_LEGACY_TASK_ID,),
        ).fetchall()
        assert [str(row[0]) for row in observations] == list(SHARED_LEGACY_ESCROWS)
        for row in observations:
            fact = json.loads(str(row[2]))
            assert fact["lock_provenance"]["transaction_observations"] == expected[str(row[1])]
        result = audit_publications(connection)
        assert result.publications_clean, result.findings
    finally:
        connection.close()
    _migrate_v4_source(source, backup_id="shared-before-mp08")
    _assert_startup_accepts(source.database)


@pytest.mark.parametrize("case", _FACT_CASES, ids=lambda case: case.id)
def test_self_consistent_observation_hash_never_erases_strict_json_types(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    case: _FactCase,
) -> None:
    if case.fixture == "native":
        template = request.getfixturevalue("native_v4_template")[0]
    else:
        template = request.getfixturevalue("legacy_v4_template")
    assert isinstance(template, Path)
    database = _copy_database(template, tmp_path / f"{case.id}.db")
    _corrupt_observed_fact(database, case)
    _assert_audit_and_startup_reject(database, None)


@pytest.mark.parametrize("field", _PUBLISHED_INTEGER_FIELDS)
@pytest.mark.parametrize("replacement", ["bool", "float"])
def test_published_result_integer_types_are_immutable(
    native_v4_template: tuple[Path, str],
    tmp_path: Path,
    field: str,
    replacement: str,
) -> None:
    template, operation_id = native_v4_template
    database = _copy_database(template, tmp_path / f"published-{field}-{replacement}.db")
    connection = sqlite3.connect(database)
    try:
        raw = connection.execute(
            "SELECT result_json FROM board_task_publications WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert raw is not None
        result = json.loads(str(raw[0]))
        result[field] = _replacement(result[field], replacement)
        connection.execute(
            "UPDATE board_task_publications SET result_json=? WHERE operation_id=?",
            (_canonical_json(result), operation_id),
        )
        connection.commit()
    finally:
        connection.close()
    _assert_audit_and_startup_reject(database, "publication_result_mismatch")


def _migrate_native_source(root: Path) -> V3Source:
    source = prepare_v3_source(root)
    writer = open_v3_writer(source)
    try:
        add_rich_cutover_history(writer)
    finally:
        writer.close()
    return _migrate(source, backup_id="native-audit-before-mp05")


def _migrate_legacy_source(root: Path) -> V3Source:
    return _migrate(
        prepare_v3_source_with_legacy_refund(root),
        backup_id="legacy-audit-before-mp05",
    )


def _migrate(source: V3Source, *, backup_id: str) -> V3Source:
    publish_verified_backup(source, backup_id=backup_id)
    result = migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v4_verified"
    _migrate_v4_source(source, backup_id=f"{backup_id}-v5")
    return source


def _migrate_v4_source(source: V3Source, *, backup_id: str) -> None:
    publish_verified_backup(source, backup_id=backup_id)
    result = migrate_v4_to_v5(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
        current_task_board_caller_kid=source.caller_kid,
    )
    assert result.observation == "committed_v5_verified"


def _publish_native_task(source: V3Source) -> str:
    writer = DbWriter(
        str(source.database), 5000, "wal", None, current_task_board_caller_kid="gateway-task-board"
    )
    try:
        poster_id = create_funded_payer(writer, name="Publication audit poster")
        intent = publication_intent(poster_id)
        command = parse_task_publication_request(intent.body())
        admitted = writer.admit_task_publication(command, source.caller_kid)
        assert admitted["publication_state"] == "awaiting_lock"
        _lock_current_escrow(writer, intent)
        published = writer.reconcile_task_publication(intent.operation_id, source.caller_kid)
        assert published["publication_state"] == "published"
        return intent.operation_id
    finally:
        writer.close()


def _lock_current_escrow(writer: DbWriter, intent: Any) -> None:
    committed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    result = writer.escrow_lock(
        {
            "operation_id": intent.lock_operation_id,
            "escrow_id": f"esc-{uuid4()}",
            "payer_account_id": intent.poster_id,
            "amount": intent.reward,
            "task_id": intent.task_id,
            "created_at": committed_at,
            "tx_id": f"tx-{uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": committed_at,
                "task_id": intent.task_id,
                "agent_id": intent.poster_id,
                "summary": "Publication audit native lock",
                "payload": "{}",
            },
        }
    )
    assert result["receipt"]["operation_id"] == intent.lock_operation_id


def _legacy_transactions_by_payer(database: Path) -> dict[str, list[dict[str, object]]]:
    connection = _open_immutable(database)
    try:
        return {
            payer: [
                {
                    "tx_id": row[0],
                    "observed_kind": row[1],
                    "observed_fact_sha256": row[2],
                }
                for row in connection.execute(
                    "SELECT o.tx_id,o.observed_kind,o.observed_fact_sha256 "
                    "FROM bank_legacy_transaction_observations o "
                    "JOIN bank_transactions t ON t.tx_id=o.tx_id "
                    "WHERE t.type='escrow_lock' AND t.reference=? AND t.account_id=? "
                    "ORDER BY o.tx_id",
                    (SHARED_LEGACY_TASK_ID, payer),
                ).fetchall()
            ]
            for payer in ("a-external", "a-worker")
        }
    finally:
        connection.close()


def _corrupt_observed_fact(database: Path, case: _FactCase) -> None:
    connection = sqlite3.connect(database)
    try:
        selector = "state='refund_pending'" if case.selector == "pending" else "state='refunded'"
        raw = connection.execute(
            "SELECT observation_id,observed_fact_json FROM board_legacy_posting_observations "
            f"WHERE {selector} ORDER BY observation_id LIMIT 1"  # nosec B608
        ).fetchone()
        assert raw is not None
        fact = json.loads(str(raw[1]))
        parent, leaf = _path_parent(fact, case.path)
        parent[leaf] = _replacement(parent[leaf], case.replacement)
        fact_json = _canonical_json(fact)
        connection.execute(
            "UPDATE board_legacy_posting_observations "
            "SET observed_fact_json=?,observed_fact_sha256=? WHERE observation_id=?",
            (fact_json, _sha256(fact_json), raw[0]),
        )
        attestation = [
            [row[0], row[1], row[2]]
            for row in connection.execute(
                "SELECT observation_id,escrow_id,observed_fact_sha256 "
                "FROM board_legacy_posting_observations ORDER BY observation_id COLLATE BINARY"
            ).fetchall()
        ]
        connection.execute(
            "UPDATE board_publication_cutover SET legacy_observations_sha256=?",
            (_sha256(_canonical_json(attestation)),),
        )
        connection.commit()
    finally:
        connection.close()


def _path_parent(value: dict[str, Any], path: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    current = value
    for segment in path[:-1]:
        child = current[segment]
        assert isinstance(child, dict)
        current = child
    return current, path[-1]


def _replacement(value: object, replacement: str) -> object:
    if replacement == "bool":
        assert type(value) is int
        return bool(value)
    if replacement == "float":
        assert type(value) is int
        return float(value)
    return replacement


def _assert_audit_and_startup_reject(database: Path, expected_code: str | None) -> None:
    before = _database_files(database)
    connection = _open_immutable(database)
    try:
        try:
            result = audit_publications(connection)
        except Exception as exc:
            pytest.fail(f"Publication audit crashed instead of rejecting corruption: {exc!r}")
    finally:
        connection.close()
    assert not result.publications_clean
    if expected_code is not None:
        assert expected_code in {finding.code for finding in result.findings}
    assert _database_files(database) == before
    writer: DbWriter | None = None
    try:
        with pytest.raises(ServiceError) as raised:
            writer = DbWriter(
                str(database), 5000, "wal", None, current_task_board_caller_kid="gateway-task-board"
            )
        assert raised.value.error == "database_publication_contract_failed"
        if expected_code is not None:
            assert expected_code in raised.value.details["finding_codes"]
    finally:
        if writer is not None:
            writer.close()
    assert database.read_bytes() == before[database.name]


def _assert_startup_accepts(database: Path) -> None:
    before = database.read_bytes()
    writer = DbWriter(
        str(database), 5000, "wal", None, current_task_board_caller_kid="gateway-task-board"
    )
    writer.close()
    assert database.read_bytes() == before


def _copy_database(source: Path, target: Path) -> Path:
    _assert_standalone(source)
    shutil.copy2(source, target)
    return target


def _assert_standalone(database: Path) -> None:
    wal = Path(f"{database}-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def _open_immutable(database: Path) -> sqlite3.Connection:
    _assert_standalone(database)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _database_files(database: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm"))
        if path.exists()
    }


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
