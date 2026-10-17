"""Failing-first coverage for the fixed offline MP-06 v0-to-v1 migration."""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import public_key_to_b64
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock

from db_gateway_service import recovery
from db_gateway_service.money_audit import audit_money
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from tests.unit import test_mp06_recovery as recovery_tests

if TYPE_CHECKING:
    from collections.abc import Iterator

    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _legacy_sql(*, historical: bool) -> str:
    if not historical:
        return load_schema_sql(SchemaState.SUPPORTED_V0)
    root = Path(__file__).parents[2] / "src/db_gateway_service/schema_resources"
    sql = (root / "legacy_base.sql").read_text()
    return (
        sql
        + "\n"
        + "\n".join(
            (
                "ALTER TABLE board_tasks ADD COLUMN dispute_id TEXT;",
                "ALTER TABLE board_tasks ADD COLUMN rebuttal_deadline TEXT;",
                "ALTER TABLE board_tasks ADD COLUMN rebuttal_submitted_at TEXT;",
                "ALTER TABLE bank_transactions ADD COLUMN event_id INTEGER;",
                "ALTER TABLE bank_escrow ADD COLUMN event_id INTEGER;",
            )
        )
    )


def _replace_database_with_v0(
    connection: sqlite3.Connection,
    *,
    source_root: Path,
    historical: bool,
    malformed_money: bool,
) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    for table in tables:
        connection.execute(f'DROP TABLE "{table}"')  # nosec B608
    connection.commit()
    connection.executescript(_legacy_sql(historical=historical))
    canonical_public_key = "ed25519:" + public_key_to_b64(Ed25519PrivateKey.generate().public_key())
    connection.execute(
        "INSERT INTO identity_agents VALUES ('a-external', 'External', ?, 'now')",
        (canonical_public_key,),
    )
    worker_public_key = "ed25519:" + public_key_to_b64(Ed25519PrivateKey.generate().public_key())
    connection.execute(
        "INSERT INTO identity_agents VALUES ('a-worker', 'Worker', ?, 'now')",
        (worker_public_key,),
    )
    event_ids: list[int] = []
    for event_type in (
        "account.created",
        "escrow.locked",
        "task.created",
        "bid.submitted",
        "claim.filed",
        "rebuttal.submitted",
    ):
        cursor = connection.execute(
            "INSERT INTO events "
            "(event_source, event_type, timestamp, task_id, agent_id, summary) "
            "VALUES ('bank', ?, 'now', 't-1', 'a-external', ?)",
            (event_type, event_type),
        )
        event_ids.append(int(cursor.lastrowid))
    balance: object = "bad" if malformed_money else 50
    connection.execute("INSERT INTO bank_accounts VALUES ('a-external', ?, 'now')", (balance,))
    connection.execute("INSERT INTO bank_accounts VALUES ('a-worker', 0, 'now')")
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-1', 'a-external', 'credit', 150, 150, 'initial', 'now', ?)",
        (event_ids[0],),
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-lock', 'a-external', 'escrow_lock', 50, 100, 't-1', 'now', ?)",
        (event_ids[1],),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
        "VALUES ('esc-1', 'a-external', 50, 't-1', 'locked', 'now', ?)",
        (event_ids[1],),
    )
    connection.execute(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, escrow_id, created_at) "
        "VALUES ('t-1', 'a-external', 'Task', 'Spec', 50, 'disputed', 1, 1, 1, 'now', "
        "'esc-1', 'now')"
    )
    connection.execute(
        "UPDATE board_tasks SET worker_id='a-worker', dispute_reason='reason', "
        "dispute_id='clm-1', rebuttal_deadline='later', rebuttal_submitted_at='now', "
        "disputed_at='now' WHERE task_id='t-1'"
    )
    connection.execute(
        "INSERT INTO board_bids "
        "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
        "VALUES ('bid-1', 't-1', 'a-worker', 'proposal', 50, 'now')"
    )
    connection.execute(
        "INSERT INTO court_claims "
        "(claim_id, task_id, claimant_id, respondent_id, reason, status, "
        "rebuttal_deadline, filed_at) "
        "VALUES ('clm-1', 't-1', 'a-external', 'a-worker', 'reason', 'rebuttal', "
        "'later', 'now')"
    )
    connection.execute(
        "INSERT INTO court_rebuttals "
        "(rebuttal_id, claim_id, agent_id, content, submitted_at) "
        "VALUES ('reb-1', 'clm-1', 'a-worker', 'response', 'now')"
    )
    connection.execute(
        "INSERT INTO bank_transactions "
        "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
        "VALUES ('tx-lock-bank-only', 'a-external', 'escrow_lock', 50, 50, "
        "'t-bank-only', 'now', ?)",
        (event_ids[1],),
    )
    connection.execute(
        "INSERT INTO bank_escrow "
        "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
        "VALUES ('esc-bank-only', 'a-external', 50, 't-bank-only', 'locked', 'now', ?)",
        (event_ids[1],),
    )
    asset = source_root / "assets/task-1/asset-1/answer.txt"
    connection.execute(
        "INSERT INTO board_assets "
        "(asset_id, task_id, uploader_id, filename, content_type, size_bytes, "
        "storage_path, content_hash, uploaded_at) "
        "VALUES ('asset-1', 't-1', 'a-external', 'answer.txt', 'text/plain', ?, ?, ?, 'now')",
        (asset.stat().st_size, str(asset), hashlib.sha256(asset.read_bytes()).hexdigest()),
    )
    connection.commit()


def _prepared_v0(
    tmp_path: Path,
    *,
    historical: bool = False,
    malformed_money: bool = False,
    duplicate_escrow: bool = False,
) -> tuple[RecoveryInventoryV1, Path, Path, Path]:
    inventory, connection = recovery_tests._source(tmp_path)
    _replace_database_with_v0(
        connection,
        source_root=inventory.source_root,
        historical=historical,
        malformed_money=malformed_money,
    )
    if duplicate_escrow:
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at, event_id) "
            "VALUES ('esc-duplicate', 'a-external', 50, 't-1', 'released', 'now', 2)"
        )
        connection.commit()
    connection.close()
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp06",
        work_root=work_root,
    )
    database = inventory.source_root / "data/economy.db"
    return inventory, backup_root, work_root, database


def _migrate(
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    work_root: Path,
) -> object:
    from db_gateway_service.migration import migrate_v0_to_v1

    return migrate_v0_to_v1(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp06",
        work_root=work_root,
        busy_timeout_ms=5000,
    )


def test_migration_api_exists_without_collection_failure() -> None:
    try:
        module = importlib.import_module("db_gateway_service.migration")
    except ModuleNotFoundError:
        pytest.fail("migration API is not implemented")
    assert callable(module.migrate_v0_to_v1)


def test_v1_structural_table_delta_is_exactly_the_fixed_rebuild_set() -> None:
    legacy = sqlite3.connect(":memory:")
    current = sqlite3.connect(":memory:")
    try:
        legacy.executescript(load_schema_sql(SchemaState.SUPPORTED_V0))
        current.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        legacy_tables = dict(
            legacy.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
        current_tables = dict(
            current.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        )
    finally:
        legacy.close()
        current.close()

    changed = {
        table
        for table in legacy_tables.keys() | current_tables.keys()
        if legacy_tables.get(table) != current_tables.get(table)
    }
    assert changed == {
        "schema_metadata",
        "bank_accounts",
        "bank_transactions",
        "bank_escrow",
        "board_tasks",
        "board_bids",
    }


@pytest.mark.parametrize("historical", [False, True])
def test_complete_v0_layout_migrates_exactly_and_preserves_data(
    tmp_path: Path, historical: bool
) -> None:
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path, historical=historical)
    backup_digest = _tree_digest(backup_root / "before-mp06")
    asset = inventory.source_root / "assets/task-1/asset-1/answer.txt"
    asset_bytes = asset.read_bytes()
    key_paths = sorted((inventory.source_root / "keys").glob("*"))
    key_bytes = {path.name: path.read_bytes() for path in key_paths}

    result = _migrate(inventory, backup_root, work_root)

    assert result.applied is True
    assert result.schema_version == 1
    assert result.backup_id == "before-mp06"
    assert result.observation == "committed_v1_verified"
    assert result.evidence_path.is_file()
    assert _tree_digest(backup_root / "before-mp06") == backup_digest
    assert asset.read_bytes() == asset_bytes
    assert {path.name: path.read_bytes() for path in key_paths} == key_bytes
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
        assert audit_money(connection).financially_clean is True
        metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
        assert metadata[0:4] == (1, 1, 0, "before-mp06")
        assert metadata[4] == result.manifest_sha256
        assert connection.execute("SELECT balance FROM bank_accounts").fetchone() == (50,)
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone() == (6,)
        assert connection.execute("SELECT amount FROM board_bids").fetchone() == (50,)
        assert connection.execute(
            "SELECT dispute_id, rebuttal_deadline, rebuttal_submitted_at FROM board_tasks"
        ).fetchone() == ("clm-1", "later", "now")
        assert connection.execute("SELECT content FROM court_rebuttals").fetchone() == ("response",)
        asset_row = connection.execute(
            "SELECT task_id, uploader_id, storage_path, content_hash FROM board_assets"
        ).fetchone()
        assert asset_row == (
            "t-1",
            "a-external",
            str(asset),
            hashlib.sha256(asset_bytes).hexdigest(),
        )
        assert connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='events'"
        ).fetchone() == (6,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


@pytest.mark.parametrize("busy_timeout_ms", [True, -1, 2_147_483_648])
def test_invalid_timeout_is_rejected_before_backup_context(
    monkeypatch: pytest.MonkeyPatch, busy_timeout_ms: int
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    entered = False

    def forbidden_context(*_args: object, **_kwargs: object) -> Iterator[object]:
        nonlocal entered
        entered = True
        raise AssertionError("backup context must not be entered")
        yield

    monkeypatch.setattr(module, "open_backup_bound_source", forbidden_context)
    with pytest.raises(module.MigrationError) as raised:
        module.migrate_v0_to_v1(
            object(),
            backup_root=Path("/unused"),
            backup_id="unused",
            work_root=Path("/unused-work"),
            busy_timeout_ms=busy_timeout_ms,
        )
    assert raised.value.reason == "invalid_argument"
    assert entered is False


@pytest.mark.parametrize(
    "fault_point",
    [
        *(
            f"after_drop:{table}"
            for table in (
                "board_bids",
                "board_tasks",
                "bank_transactions",
                "bank_escrow",
                "bank_accounts",
            )
        ),
        *(
            f"after_create:{table}"
            for table in (
                "bank_accounts",
                "bank_transactions",
                "bank_escrow",
                "board_tasks",
                "board_bids",
            )
        ),
        *(
            f"after_copy:{table}"
            for table in (
                "bank_accounts",
                "bank_transactions",
                "bank_escrow",
                "board_tasks",
                "board_bids",
            )
        ),
        *(
            f"after_index:{index}"
            for index in (
                "idx_bank_tx_idempotent",
                "idx_bank_tx_history",
                "idx_bank_escrow_lifetime",
                "idx_board_tasks_status",
                "idx_board_tasks_poster",
                "idx_board_tasks_worker",
                "idx_board_tasks_created",
                "idx_board_bids_one_per_agent",
            )
        ),
        "after_metadata",
        "before_validation",
        "before_commit",
    ],
)
def test_precommit_fault_rolls_back_exact_v0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_point: str
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path)
    before_connection = sqlite3.connect(database)
    try:
        before = tuple(before_connection.iterdump())
    finally:
        before_connection.close()

    def fail_at(point: str) -> None:
        if point == fault_point:
            raise RuntimeError(f"fault:{point}")

    monkeypatch.setattr(module, "_fault", fail_at)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "migration_failed"
    connection = sqlite3.connect(database)
    try:
        assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
        assert tuple(connection.iterdump()) == before
    finally:
        connection.close()


def test_malformed_finance_is_blocked_without_repair(tmp_path: Path) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path, malformed_money=True)
    before = database.read_bytes()
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "backup_not_admitted"
    assert database.read_bytes() == before


def test_historical_duplicate_escrows_report_record_ids_before_live_rw_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path, duplicate_escrow=True)
    before = database.read_bytes()
    opened_rw = False

    def forbidden_writer(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        nonlocal opened_rw
        opened_rw = True
        raise AssertionError("live source must not open read-write")

    monkeypatch.setattr(module, "_open_live_writer", forbidden_writer)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "financial_audit_failed"
    diagnostics = raised.value.safe_details["findings"]
    duplicate = next(
        finding for finding in diagnostics if finding["code"] == "duplicate_lifetime_escrow"
    )
    assert duplicate == {
        "code": "duplicate_lifetime_escrow",
        "table": "bank_escrow",
        "column": "payer_account_id,task_id",
        "record_ids": ["a-external", "t-1", "esc-1", "esc-duplicate"],
    }
    assert opened_rw is False
    assert database.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "tampered"])
def test_incomplete_or_tampered_backup_is_rejected_before_live_rw_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    marker = backup_root / "before-mp06/COMPLETE"
    if damage == "missing":
        marker.unlink()
    else:
        marker.write_bytes(marker.read_bytes() + b"tamper")
    opened_rw = False

    def forbidden_writer(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        nonlocal opened_rw
        opened_rw = True
        raise AssertionError("live source must not open read-write")

    monkeypatch.setattr(module, "_open_live_writer", forbidden_writer)
    with pytest.raises(module.MigrationError):
        _migrate(inventory, backup_root, work_root)
    assert opened_rw is False


def test_inventory_mismatch_and_busy_lock_block_live_rw_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    opened_rw = False

    def forbidden_writer(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        nonlocal opened_rw
        opened_rw = True
        raise AssertionError("live source must not open read-write")

    monkeypatch.setattr(module, "_open_live_writer", forbidden_writer)
    changed = inventory.model_copy(update={"runtime_id": "different-runtime"})
    with pytest.raises(module.MigrationError):
        _migrate(changed, backup_root, work_root)
    assert opened_rw is False

    with (
        hold_maintenance_lock(
            inventory.maintenance_lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ),
        pytest.raises(module.MigrationError),
    ):
        _migrate(inventory, backup_root, work_root)
    assert opened_rw is False


def test_intervening_live_change_is_rejected_inside_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path)
    original_open = module._open_live_writer

    def open_after_external_change(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
        writer = original_open(path, busy_timeout_ms)
        external = sqlite3.connect(path)
        try:
            external.execute(
                "INSERT INTO events "
                "(event_source, event_type, timestamp, agent_id, summary) "
                "VALUES ('identity', 'external.change', 'later', 'a-external', 'changed')"
            )
            external.commit()
        finally:
            external.close()
        return writer

    monkeypatch.setattr(module, "_open_live_writer", open_after_external_change)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "source_changed"
    connection = sqlite3.connect(database)
    try:
        assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone() == (7,)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("persist", "observation"),
    [(False, "not_applied_v0_observed"), (True, "committed_v1_observed")],
)
def test_commit_exception_is_uncertain_and_observed_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persist: bool,
    observation: str,
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)

    def uncertain_commit(connection: sqlite3.Connection) -> None:
        if persist:
            connection.execute("COMMIT").close()
        raise sqlite3.OperationalError("injected commit uncertainty")

    monkeypatch.setattr(module, "_commit", uncertain_commit)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "commit_outcome_uncertain"
    assert raised.value.safe_details["observation"] == observation
    assert Path(str(raised.value.safe_details["evidence_path"])).is_file()


@pytest.mark.parametrize(
    ("rollback_fails", "close_fails"),
    [(True, False), (False, True), (True, True)],
    ids=["rollback-only", "close-only", "both"],
)
def test_precommit_cleanup_failures_use_native_disposal_before_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollback_fails: bool,
    close_fails: bool,
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path)
    retained: list[sqlite3.Connection] = []
    original_open = module._open_live_writer

    def hostile_open(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
        connection = original_open(path, busy_timeout_ms)
        retained.append(connection)
        return connection

    def refusing_rollback(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected rollback refusal")

    def refusing_close(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected close refusal")

    def fail_before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("primary-before-commit")

    monkeypatch.setattr(module, "_open_live_writer", hostile_open)
    if rollback_fails:
        monkeypatch.setattr(module, "_rollback_connection", refusing_rollback)
    if close_fails:
        monkeypatch.setattr(module, "_dynamic_close", refusing_close)
    monkeypatch.setattr(module, "_fault", fail_before_commit)
    try:
        with pytest.raises(module.MigrationError) as raised:
            _migrate(inventory, backup_root, work_root)
        assert raised.value.reason == "migration_failed"
        assert raised.value.safe_details["observation"] == "not_applied_v0_observed"
        assert raised.value.safe_details["native_close_completed"] is True
        expected_cleanup_codes = []
        if rollback_fails:
            expected_cleanup_codes.append("rollback_method_failed")
        if close_fails:
            expected_cleanup_codes.append("close_method_failed")
        expected_cleanup_codes.append("native_close_completed")
        assert raised.value.safe_details["cleanup_codes"] == expected_cleanup_codes
        with pytest.raises(sqlite3.ProgrammingError):
            retained[0].execute("SELECT 1")
        connection = sqlite3.connect(database)
        try:
            assert classify_schema(connection).state is SchemaState.SUPPORTED_V0
        finally:
            connection.close()
    finally:
        for connection in retained:
            with contextlib.suppress(sqlite3.Error):
                sqlite3.Connection.close(connection)


def test_live_writer_open_failure_is_structured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)

    def fail_open(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("injected rw open failure")

    monkeypatch.setattr(module, "_open_live_writer", fail_open)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "source_open_failed"


def test_clone_close_failure_does_not_mask_admission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    original_open = module._open_read_only

    class FailingCloseClone(sqlite3.Connection):
        def close(self) -> None:
            super().close()
            raise sqlite3.OperationalError("secondary clone close failure")

    def invalid_clone(_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:", factory=FailingCloseClone)
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        return connection

    monkeypatch.setattr(module, "_open_read_only", invalid_clone)
    try:
        with pytest.raises(module.MigrationError) as raised:
            _migrate(inventory, backup_root, work_root)
        assert raised.value.reason == "unsupported_schema"
        assert any("clone close" in note for note in raised.value.__notes__)
    finally:
        monkeypatch.setattr(module, "_open_read_only", original_open)


def test_evidence_failure_preserves_primary_after_writer_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    retained: list[sqlite3.Connection] = []
    original_open = module._open_live_writer

    def recording_open(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
        connection = original_open(path, busy_timeout_ms)
        retained.append(connection)
        return connection

    def fail_before_commit(point: str) -> None:
        if point == "before_commit":
            raise RuntimeError("primary-before-commit")

    def fail_evidence(*_args: object, **_kwargs: object) -> Path:
        raise OSError("secondary evidence failure")

    monkeypatch.setattr(module, "_open_live_writer", recording_open)
    monkeypatch.setattr(module, "_fault", fail_before_commit)
    monkeypatch.setattr(recovery.BackupBoundSource, "write_evidence", fail_evidence)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "migration_failed"
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert str(raised.value.__cause__) == "primary-before-commit"
    assert any("evidence publication" in note for note in raised.value.__cause__.__notes__)
    with pytest.raises(sqlite3.ProgrammingError):
        retained[0].execute("SELECT 1")


def test_fresh_validation_close_failure_preserves_validation_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")

    class FailingCloseConnection(sqlite3.Connection):
        def close(self) -> None:
            super().close()
            raise sqlite3.OperationalError("secondary fresh close failure")

    connection = sqlite3.connect(":memory:", factory=FailingCloseConnection)

    def open_clone(_path: Path) -> sqlite3.Connection:
        return connection

    def fail_validation(*_args: object, **_kwargs: object) -> None:
        raise module.MigrationError("validation_failed", "intended validation failure", {})

    monkeypatch.setattr(module, "_open_read_only", open_clone)
    monkeypatch.setattr(module, "_validate_migrated_transaction", fail_validation)
    with pytest.raises(module.MigrationError) as raised:
        module._validate_fresh_v1(
            tmp_path / "unused.db",
            admission=cast("Any", object()),
            backup_id="backup",
            manifest_sha256="a" * 64,
        )
    assert raised.value.reason == "validation_failed"
    assert any("fresh validation connection close" in note for note in raised.value.__notes__)


def test_postcommit_dynamic_close_failure_reports_native_disposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    retained: list[sqlite3.Connection] = []
    original_open = module._open_live_writer

    def recording_open(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
        connection = original_open(path, busy_timeout_ms)
        retained.append(connection)
        return connection

    def fail_dynamic_close(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("sensitive dynamic close failure")

    monkeypatch.setattr(module, "_open_live_writer", recording_open)
    monkeypatch.setattr(module, "_dynamic_close", fail_dynamic_close)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "post_commit_cleanup_failed"
    assert raised.value.safe_details["observation"] == "committed_v1_observed"
    assert raised.value.safe_details["cleanup_codes"] == [
        "close_method_failed",
        "native_close_completed",
    ]
    assert raised.value.safe_details["native_close_completed"] is True
    with pytest.raises(sqlite3.ProgrammingError):
        retained[0].execute("SELECT 1")


def test_backup_clone_open_failure_is_redacted_before_live_rw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    opened_rw = False

    def fail_clone(_path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("sensitive clone engine detail")

    def forbidden_writer(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        nonlocal opened_rw
        opened_rw = True
        raise AssertionError("live source must not open read-write")

    monkeypatch.setattr(module, "_open_read_only", fail_clone)
    monkeypatch.setattr(module, "_open_live_writer", forbidden_writer)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "backup_clone_open_failed"
    assert "sensitive" not in raised.value.message
    assert raised.value.safe_details == {}
    assert opened_rw is False


def test_postcommit_close_and_evidence_failures_preserve_cleanup_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)
    retained: list[sqlite3.Connection] = []
    original_open = module._open_live_writer

    def recording_open(path: Path, busy_timeout_ms: int) -> sqlite3.Connection:
        connection = original_open(path, busy_timeout_ms)
        retained.append(connection)
        return connection

    def fail_dynamic_close(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("dynamic close primary")

    def fail_evidence(*_args: object, **_kwargs: object) -> Path:
        raise OSError("evidence secondary")

    monkeypatch.setattr(module, "_open_live_writer", recording_open)
    monkeypatch.setattr(module, "_dynamic_close", fail_dynamic_close)
    monkeypatch.setattr(recovery.BackupBoundSource, "write_evidence", fail_evidence)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "post_commit_cleanup_failed"
    assert raised.value.safe_details == {
        "observation": "committed_v1_observed",
        "cleanup_codes": ["close_method_failed", "native_close_completed"],
        "native_close_completed": True,
    }
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert any("evidence publication" in note for note in raised.value.__notes__)
    with pytest.raises(sqlite3.ProgrammingError):
        retained[0].execute("SELECT 1")


def test_postcommit_validation_evidence_contains_cleanup_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = importlib.import_module("db_gateway_service.migration")
    inventory, backup_root, work_root, _database = _prepared_v0(tmp_path)

    def fail_dynamic_close(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("dynamic close alongside validation")

    def fail_fresh(*_args: object, **_kwargs: object) -> None:
        raise module.MigrationError("validation_failed", "fresh validation primary", {})

    monkeypatch.setattr(module, "_dynamic_close", fail_dynamic_close)
    monkeypatch.setattr(module, "_validate_fresh_v1", fail_fresh)
    with pytest.raises(module.MigrationError) as raised:
        _migrate(inventory, backup_root, work_root)
    assert raised.value.reason == "post_commit_validation_failed"
    evidence = json.loads(Path(str(raised.value.safe_details["evidence_path"])).read_text())
    assert evidence["cleanup_codes"] == ["close_method_failed", "native_close_completed"]
    assert evidence["native_close_completed"] is True
