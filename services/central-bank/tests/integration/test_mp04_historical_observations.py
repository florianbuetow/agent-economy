"""Public Bank propagation of attested MP-04 historical observations.

Bank HTTP, its production ``LedgerDbClient``, signed Gateway ASGI requests, the
real Gateway writers, migration, and SQLite are integrated here. As in the
existing Bank integration fixture, Identity verification is mocked locally;
the Gateway request signer and its verification remain real.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from db_gateway_service import recovery
from db_gateway_service.effect_recovery_audit import audit_effect_recovery
from db_gateway_service.migration import (
    MigrationError,
    migrate_v1_to_v2,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
)
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.task_contract_audit import audit_task_contracts
from service_auth import generate_keypair, public_key_to_b64
from service_auth.signing import keypair_fingerprint
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    PathResolutionBase,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceKeypairV1,
    SourceResourceV1,
)

from tests.integration.conftest import PLATFORM_AGENT_ID, make_jws_token

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient, Response

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_PAYER = "a-historical-payer"
_WORKER = "a-historical-worker"
_CREDIT_REFERENCE = "historical-genesis"
_V1_BACKUP_ID = "before-mp04-public-history"
_V2_BACKUP_ID = "before-mp07-bank-history"
_V3_BACKUP_ID = "before-mp05-publication-history"
_V4_BACKUP_ID = "before-mp08-effect-recovery-history"
_RELEASED_TASK_ID = "task-released"
_SPLIT_TASK_ID = "task-split"


def _hash(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _file_hashes(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mode & 0o777,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _seed_v1(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        payer_public_key = "ed25519:" + public_key_to_b64(Ed25519PrivateKey.generate().public_key())
        worker_public_key = "ed25519:" + public_key_to_b64(
            Ed25519PrivateKey.generate().public_key()
        )
        connection.executemany(
            "INSERT INTO identity_agents VALUES (?, ?, ?, '')",
            [
                (_PAYER, "Historical Payer", payer_public_key),
                (_WORKER, "Historical Worker", worker_public_key),
            ],
        )
        connection.executemany(
            "INSERT INTO bank_accounts VALUES (?, ?, '')",
            [(_PAYER, 640), (_WORKER, 160)],
        )
        connection.executemany(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("tx-historical-credit", _PAYER, "credit", 1000, 1000, _CREDIT_REFERENCE, "", None),
                ("tx-lock-open", _PAYER, "escrow_lock", 100, 900, "task-open", "", None),
                ("tx-lock-released", _PAYER, "escrow_lock", 100, 800, "task-released", "", None),
                ("tx-release", _WORKER, "escrow_release", 100, 100, "esc-released", "", None),
                ("tx-lock-split", _PAYER, "escrow_lock", 100, 700, "task-split", "", None),
                ("tx-split-worker", _WORKER, "escrow_release", 60, 160, "esc-split", "", None),
                ("tx-split-poster", _PAYER, "escrow_release", 40, 740, "esc-split", "", None),
                ("tx-lock-native", _PAYER, "escrow_lock", 100, 640, "task-native", "", None),
            ],
        )
        connection.executemany(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at, "
            "resolved_at, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("esc-open", _PAYER, 100, "task-open", "locked", "", None, None),
                ("esc-released", _PAYER, 100, "task-released", "released", "", "", None),
                ("esc-split", _PAYER, 100, "task-split", "split", "", "", None),
                ("esc-native", _PAYER, 100, "task-native", "locked", "", None, None),
            ],
        )
        _seed_historical_task_context(connection)
        settled_at = "2026-03-01T11:00:00Z"
        event = connection.execute(
            "INSERT INTO events "
            "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
            "VALUES ('central-bank','escrow.released',?,?,?,?,?)",
            (
                settled_at,
                _RELEASED_TASK_ID,
                _WORKER,
                "Preserved historical worker settlement",
                "{}",
            ),
        )
        assert event.lastrowid is not None
        release_event_id = int(event.lastrowid)
        connection.execute(
            "UPDATE bank_transactions SET timestamp=?,event_id=? WHERE tx_id='tx-release'",
            (settled_at, release_event_id),
        )
        connection.execute(
            "UPDATE bank_escrow SET resolved_at=? WHERE escrow_id='esc-released'",
            (settled_at,),
        )
        connection.commit()
    finally:
        connection.close()


def _seed_historical_task_context(connection: sqlite3.Connection) -> None:
    """Attach true Board history to settled worker payouts without changing Bank rows."""
    created_at = "2026-03-01T08:00:00Z"
    bidding_deadline = "2026-03-01T09:00:00Z"
    accepted_at = bidding_deadline
    execution_deadline = "2026-03-01T10:00:00Z"
    submitted_at = execution_deadline
    review_deadline = "2026-03-01T11:00:00Z"
    connection.executemany(
        "INSERT INTO board_tasks "
        "(task_id, poster_id, title, spec, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, bid_count, escrow_pending, escrow_id, worker_id, accepted_bid_id, "
        "dispute_reason, dispute_id, rebuttal_deadline, created_at, accepted_at, submitted_at, "
        "approved_at, disputed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 3600, 3600, 3600, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?)",
        [
            (
                _RELEASED_TASK_ID,
                _PAYER,
                "Historical approved task",
                "Preserve the existing released worker payout",
                100,
                "approved",
                bidding_deadline,
                execution_deadline,
                review_deadline,
                "esc-released",
                _WORKER,
                "bid-released",
                None,
                None,
                None,
                created_at,
                accepted_at,
                submitted_at,
                review_deadline,
                None,
            ),
            (
                _SPLIT_TASK_ID,
                _PAYER,
                "Historical split task",
                "Preserve the existing split while Board ruling is unrecorded",
                100,
                "disputed",
                bidding_deadline,
                execution_deadline,
                review_deadline,
                "esc-split",
                _WORKER,
                "bid-split",
                "Historical split settlement before Board ruling was recorded",
                "clm-split",
                "2026-03-01T12:30:00Z",
                created_at,
                accepted_at,
                submitted_at,
                None,
                "2026-03-01T12:00:00Z",
            ),
        ],
    )
    connection.executemany(
        "INSERT INTO board_bids "
        "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                "bid-released",
                _RELEASED_TASK_ID,
                _WORKER,
                "Historical accepted bid",
                100,
                "2026-03-01T08:30:00Z",
            ),
            (
                "bid-split",
                _SPLIT_TASK_ID,
                _WORKER,
                "Historical accepted bid",
                100,
                "2026-03-01T08:30:00Z",
            ),
        ],
    )
    connection.execute(
        "INSERT INTO court_claims "
        "(claim_id, task_id, claimant_id, respondent_id, reason, status, rebuttal_deadline, "
        "filed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "clm-split",
            _SPLIT_TASK_ID,
            _PAYER,
            _WORKER,
            "Historical split settlement before Board ruling was recorded",
            "judging",
            "2026-03-01T12:30:00Z",
            "2026-03-01T12:00:00Z",
        ),
    )

    assert connection.execute(
        "SELECT status, poster_id, worker_id, accepted_bid_id, escrow_id, reward "
        "FROM board_tasks WHERE task_id=?",
        (_RELEASED_TASK_ID,),
    ).fetchone() == ("approved", _PAYER, _WORKER, "bid-released", "esc-released", 100)
    assert connection.execute(
        "SELECT status, poster_id, worker_id, accepted_bid_id, escrow_id, reward, dispute_id "
        "FROM board_tasks WHERE task_id=?",
        (_SPLIT_TASK_ID,),
    ).fetchone() == ("disputed", _PAYER, _WORKER, "bid-split", "esc-split", 100, "clm-split")
    assert connection.execute(
        "SELECT task_id, claimant_id, respondent_id, reason "
        "FROM court_claims WHERE claim_id='clm-split'"
    ).fetchone() == (
        _SPLIT_TASK_ID,
        _PAYER,
        _WORKER,
        "Historical split settlement before Board ruling was recorded",
    )
    for task_id in ("task-open", "task-native"):
        assert (
            connection.execute("SELECT 1 FROM board_tasks WHERE task_id=?", (task_id,)).fetchone()
            is None
        )


def _inventory(source: Path, database: Path) -> RecoveryInventoryV1:
    agents_root = source / "agents"
    agents_root.mkdir()
    keys = source / "keys"
    keys.mkdir()
    generate_keypair("gateway-service", keys)
    config = source / "gateway.json"
    config_value = {
        "database": {"path": str(database)},
        "recovery": {"maintenance_lock_path": str(source / "maintenance.lock")},
        "auth": {
            "trusted_callers": {
                "gateway-service": {"public_key_path": str(keys / "gateway-service.pub")}
            }
        },
    }
    config_bytes = json.dumps(config_value, sort_keys=True).encode("utf-8")
    config.write_bytes(config_bytes)
    maintenance_lock = source / "maintenance.lock"
    with hold_maintenance_lock(
        maintenance_lock,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass
    lock_stat = maintenance_lock.lstat()
    resources = (
        SourceResourceV1(
            logical_id="database.primary",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            capture_root=database.parent,
            captured_path=database,
        ),
        SourceResourceV1(
            logical_id="config.db-gateway",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            capture_root=config.parent,
            captured_path=config,
        ),
        SourceResourceV1(
            logical_id="runtime.maintenance_lock",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.RUNTIME_LOCK,
            capture_root=source,
            captured_path=maintenance_lock,
        ),
        SourceResourceV1(
            logical_id="key.gateway-service.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            capture_root=keys,
            captured_path=keys / "gateway-service.key",
        ),
        SourceResourceV1(
            logical_id="key.gateway-service.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            capture_root=keys,
            captured_path=keys / "gateway-service.pub",
        ),
    )
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id="central-bank-mp04-history",
        source_root=source,
        agents_package_root=agents_root,
        maintenance_lock_path=maintenance_lock,
        maintenance_lock_identity=MaintenanceLockIdentityV1(
            device=lock_stat.st_dev,
            inode=lock_stat.st_ino,
        ),
        resources=resources,
        configs=(
            SourceConfigV1(
                logical_id="db-gateway",
                capture_root=config.parent,
                captured_path=config,
                process_cwd=source,
                sha256=hashlib.sha256(config_bytes).hexdigest(),
            ),
        ),
        field_bindings=(
            SourceFieldBindingV1(
                binding_id="db-gateway.database.path",
                config_id="db-gateway",
                field_path=("database", "path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value=str(database),
                resolution_base=PathResolutionBase.ABSOLUTE,
                resolved_source_path=database,
                resource_id="database.primary",
            ),
            SourceFieldBindingV1(
                binding_id="db-gateway.recovery.maintenance_lock_path",
                config_id="db-gateway",
                field_path=("recovery", "maintenance_lock_path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value=str(maintenance_lock),
                resolution_base=PathResolutionBase.ABSOLUTE,
                resolved_source_path=maintenance_lock,
                resource_id="runtime.maintenance_lock",
            ),
            SourceFieldBindingV1(
                binding_id="db-gateway.auth.gateway-service.public_key_path",
                config_id="db-gateway",
                field_path=(
                    "auth",
                    "trusted_callers",
                    "gateway-service",
                    "public_key_path",
                ),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value=str(keys / "gateway-service.pub"),
                resolution_base=PathResolutionBase.ABSOLUTE,
                resolved_source_path=keys / "gateway-service.pub",
                resource_id="key.gateway-service.public",
            ),
        ),
        keypairs=(
            SourceKeypairV1(
                handle="gateway-service",
                role=KeypairRole.GATEWAY,
                private_resource_id="key.gateway-service.private",
                public_resource_id="key.gateway-service.public",
                expected_public_fingerprint_sha256=keypair_fingerprint(
                    "gateway-service", keys, allow_legacy_private_mode=False
                ),
                registration_requirement=RegistrationRequirement.FORBIDDEN,
                captured_registration_state=CapturedRegistrationState.UNREGISTERED,
                expected_registered_agent_id=None,
            ),
        ),
        process_ownership=(),
    )


def _with_task_board_authority(inventory: RecoveryInventoryV1) -> RecoveryInventoryV1:
    """Extend captured v3 source identity with the required Board migration authority."""
    source = inventory.source_root
    keys = source / "keys"
    handle = "gateway-task-board"
    generate_keypair(handle, keys)
    public_key_path = keys / f"{handle}.pub"
    private_key_path = keys / f"{handle}.key"

    gateway_config = next(
        config for config in inventory.configs if config.logical_id == "db-gateway"
    )
    gateway_value = json.loads(gateway_config.captured_path.read_text(encoding="utf-8"))
    gateway_value["auth"]["trusted_callers"][handle] = {
        "public_key_path": str(public_key_path),
        "capabilities": ["gateway.board.write"],
    }
    gateway_bytes = json.dumps(
        gateway_value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    gateway_config.captured_path.write_bytes(gateway_bytes)

    config_directory = source / "configs"
    config_directory.mkdir()
    board_directory = source / "task-board"
    board_directory.mkdir()
    board_config_path = config_directory / "task-board.yaml"
    board_bytes = json.dumps(
        {
            "db_gateway": {
                "signing": {
                    "kid": handle,
                    "private_key_path": str(private_key_path),
                }
            }
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    board_config_path.write_bytes(board_bytes)

    configs = (
        *(
            SourceConfigV1(
                logical_id=config.logical_id,
                captured_path=config.captured_path,
                capture_root=config.capture_root,
                process_cwd=config.process_cwd,
                sha256=(
                    hashlib.sha256(gateway_bytes).hexdigest()
                    if config.logical_id == "db-gateway"
                    else config.sha256
                ),
            )
            for config in inventory.configs
        ),
        SourceConfigV1(
            logical_id="task-board",
            captured_path=board_config_path,
            capture_root=config_directory,
            process_cwd=board_directory,
            sha256=hashlib.sha256(board_bytes).hexdigest(),
        ),
    )
    resources = (
        *inventory.resources,
        SourceResourceV1(
            logical_id="config.task-board",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            capture_root=config_directory,
            captured_path=board_config_path,
        ),
        SourceResourceV1(
            logical_id=f"key.{handle}.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            capture_root=keys,
            captured_path=private_key_path,
        ),
        SourceResourceV1(
            logical_id=f"key.{handle}.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            capture_root=keys,
            captured_path=public_key_path,
        ),
    )
    bindings = (
        *inventory.field_bindings,
        SourceFieldBindingV1(
            binding_id=f"db-gateway.auth.{handle}.public_key_path",
            config_id="db-gateway",
            field_path=("auth", "trusted_callers", handle, "public_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=str(public_key_path),
            resolution_base=PathResolutionBase.ABSOLUTE,
            resolved_source_path=public_key_path,
            resource_id=f"key.{handle}.public",
        ),
        SourceFieldBindingV1(
            binding_id="task-board.db_gateway.signing.private_key_path",
            config_id="task-board",
            field_path=("db_gateway", "signing", "private_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=str(private_key_path),
            resolution_base=PathResolutionBase.ABSOLUTE,
            resolved_source_path=private_key_path,
            resource_id=f"key.{handle}.private",
        ),
    )
    keypairs = (
        *inventory.keypairs,
        SourceKeypairV1(
            handle=handle,
            role=KeypairRole.GATEWAY,
            private_resource_id=f"key.{handle}.private",
            public_resource_id=f"key.{handle}.public",
            expected_public_fingerprint_sha256=keypair_fingerprint(
                handle,
                keys,
                allow_legacy_private_mode=False,
            ),
            registration_requirement=RegistrationRequirement.FORBIDDEN,
            captured_registration_state=CapturedRegistrationState.UNREGISTERED,
            expected_registered_agent_id=None,
        ),
    )
    processes = (
        *inventory.process_ownership,
        OwnedProcessV1(
            logical_id="task-board",
            config_id="task-board",
            process_cwd=board_directory,
            agents_package_root=None,
            pid=None,
            pgid=None,
            sid=None,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        ),
    )
    return RecoveryInventoryV1(
        contract_version=inventory.contract_version,
        runtime_id=inventory.runtime_id,
        source_root=inventory.source_root,
        agents_package_root=inventory.agents_package_root,
        maintenance_lock_path=inventory.maintenance_lock_path,
        maintenance_lock_identity=inventory.maintenance_lock_identity,
        resources=resources,
        configs=configs,
        field_bindings=bindings,
        keypairs=keypairs,
        process_ownership=processes,
    )


def _migrate_current_v3_to_v4(
    inventory: RecoveryInventoryV1,
    database: Path,
    backup_root: Path,
    work_root: Path,
    financial_history: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    v3_inventory = _with_task_board_authority(inventory)
    v3_backup = recovery.create_backup(
        v3_inventory,
        backup_root=backup_root,
        backup_id=_V3_BACKUP_ID,
        work_root=work_root,
    )
    assert v3_backup.state is recovery.PublicationState.COMPLETE
    v3_backup_hashes = _file_hashes(v3_backup.location)
    v3_verification = recovery.verify_backup(
        backup_root=backup_root,
        backup_id=_V3_BACKUP_ID,
        work_root=work_root,
    )
    assert v3_verification.artifact_verified and v3_verification.identity_complete
    publication_migration = migrate_v3_to_v4(
        v3_inventory,
        backup_root=backup_root,
        backup_id=_V3_BACKUP_ID,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert publication_migration.applied is True
    assert publication_migration.schema_version == 4
    assert publication_migration.observation == "committed_v4_verified"
    with closing(sqlite3.connect(database)) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V4
        assert _snapshot(str(database)) == financial_history
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            4,
            3,
            _V3_BACKUP_ID,
            publication_migration.manifest_sha256,
        )
    assert _file_hashes(v3_backup.location) == v3_backup_hashes
    v4_backup = recovery.create_backup(
        v3_inventory,
        backup_root=backup_root,
        backup_id=_V4_BACKUP_ID,
        work_root=work_root,
    )
    assert v4_backup.state is recovery.PublicationState.COMPLETE
    v4_backup_hashes = _file_hashes(v4_backup.location)
    v4_verification = recovery.verify_backup(
        backup_root=backup_root,
        backup_id=_V4_BACKUP_ID,
        work_root=work_root,
    )
    assert v4_verification.artifact_verified and v4_verification.identity_complete
    try:
        effect_migration = migrate_v4_to_v5(
            v3_inventory,
            backup_root=backup_root,
            backup_id=_V4_BACKUP_ID,
            work_root=work_root,
            busy_timeout_ms=5000,
            current_task_board_caller_kid="gateway-task-board",
        )
    except MigrationError as exc:
        pytest.fail(f"V4-to-V5 fixture migration failed: {exc.reason} {exc.safe_details}")
    assert effect_migration.applied is True
    assert effect_migration.schema_version == 5
    assert effect_migration.observation == "committed_v5_verified"
    with closing(sqlite3.connect(database)) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V5
        assert _snapshot(str(database)) == financial_history
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert audit_effect_recovery(connection).recovery_clean
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            5,
            4,
            _V4_BACKUP_ID,
            effect_migration.manifest_sha256,
        )
    assert _file_hashes(v4_backup.location) == v4_backup_hashes


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    source = tmp_path / "source"
    source.mkdir()
    database = tmp_path / "gateway.db"
    _seed_v1(database)
    inventory = _inventory(source, database)
    backup_root = tmp_path / "backups"
    work_root = tmp_path / "work"
    backup_root.mkdir(mode=0o700)
    work_root.mkdir(mode=0o700)
    v1_backup = recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=_V1_BACKUP_ID,
        work_root=work_root,
    )
    assert v1_backup.state is recovery.PublicationState.COMPLETE
    result = migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id=_V1_BACKUP_ID,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.applied is True
    assert result.schema_version == 2
    with closing(sqlite3.connect(database)) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        financial_v2 = _snapshot(str(database))
        assert audit_receipt_ownership(connection).ownership_clean

    v2_backup = recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=_V2_BACKUP_ID,
        work_root=work_root,
    )
    assert v2_backup.state is recovery.PublicationState.COMPLETE
    v2_backup_hashes = _file_hashes(v2_backup.location)
    verified = recovery.verify_backup(
        backup_root=backup_root,
        backup_id=_V2_BACKUP_ID,
        work_root=work_root,
    )
    assert verified.artifact_verified and verified.identity_complete
    task_migration = migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id=_V2_BACKUP_ID,
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert task_migration.applied is True
    assert task_migration.schema_version == 3
    assert task_migration.observation == "committed_v3_verified"
    with closing(sqlite3.connect(database)) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V3
        assert _snapshot(str(database)) == financial_v2
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM schema_metadata").fetchone() == (
            1,
            3,
            2,
            _V2_BACKUP_ID,
            v2_backup.manifest_sha256,
        )
        assert connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() == (0,)
    assert _file_hashes(v2_backup.location) == v2_backup_hashes

    _migrate_current_v3_to_v4(inventory, database, backup_root, work_root, financial_v2)
    return str(database)


def _snapshot(database: str) -> dict[str, tuple[tuple[object, ...], ...]]:
    connection = sqlite3.connect(database)
    try:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND (name='events' OR name LIKE 'bank_%') ORDER BY name"
            )
        ]
        return {
            table: tuple(
                sorted(
                    (tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')),
                    key=repr,
                )
            )
            for table in tables
        }
    finally:
        connection.close()


def _credit_token(
    platform_key: Ed25519PrivateKey,
    operation_id: str,
    amount: int,
    reference: str = _CREDIT_REFERENCE,
) -> str:
    return make_jws_token(
        platform_key,
        PLATFORM_AGENT_ID,
        {
            "action": "credit",
            "operation_id": operation_id,
            "account_id": _PAYER,
            "amount": amount,
            "reference": reference,
        },
        resource=f"POST /accounts/{_PAYER}/credit",
    )


def _lock_token(
    agent_key: Ed25519PrivateKey,
    operation_id: str,
    amount: int,
    task_id: str,
) -> str:
    return make_jws_token(
        agent_key,
        _PAYER,
        {
            "action": "escrow_lock",
            "operation_id": operation_id,
            "agent_id": _PAYER,
            "amount": amount,
            "task_id": task_id,
        },
        resource="POST /escrow/lock",
    )


async def _credit(
    client: AsyncClient,
    platform_key: Ed25519PrivateKey,
    operation_id: str,
    amount: int,
    reference: str = _CREDIT_REFERENCE,
) -> Response:
    token = _credit_token(platform_key, operation_id, amount, reference)
    return await client.post(f"/accounts/{_PAYER}/credit", json={"token": token})


async def _lock(
    client: AsyncClient,
    agent_key: Ed25519PrivateKey,
    operation_id: str,
    amount: int,
    task_id: str,
) -> Response:
    token = _lock_token(agent_key, operation_id, amount, task_id)
    return await client.post("/escrow/lock", json={"token": token})


def _settlement_token(
    platform_key: Ed25519PrivateKey,
    operation_id: str,
    escrow_id: str,
    *,
    split: bool,
) -> tuple[str, str]:
    if split:
        path = f"/escrow/{escrow_id}/split"
        payload = {
            "action": "escrow_split",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "worker_account_id": _WORKER,
            "worker_pct": 60,
            "poster_account_id": _PAYER,
            "source_reference": f"historical:{escrow_id}:split",
        }
    else:
        path = f"/escrow/{escrow_id}/release"
        payload = {
            "action": "escrow_release",
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "recipient_account_id": _WORKER,
            "source_reference": f"historical:{escrow_id}:release",
        }
    return path, make_jws_token(platform_key, PLATFORM_AGENT_ID, payload, resource=f"POST {path}")


async def test_exact_credit_and_lock_return_bound_unchanged_observations_without_aliasing(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    before = _snapshot(gateway_db_path)
    credit_operation = "op-00000000-0000-4000-8000-000000000501"
    credit = await _credit(client, platform_keypair[0], credit_operation, 1000)
    assert credit.status_code == 200, credit.text
    credit_body = credit.json()
    credit_fact = {
        "tx_id": "tx-historical-credit",
        "account_id": _PAYER,
        "type": "credit",
        "amount": 1000,
        "balance_after": 1000,
        "reference": _CREDIT_REFERENCE,
        "timestamp": "",
        "event_id": None,
    }
    assert credit_body == {
        "outcome": "legacy_observation",
        "attempt": {
            "operation_id": credit_operation,
            "operation_kind": "credit",
            "request_sha256": _hash(
                {
                    "request_version": 1,
                    "operation_kind": "credit",
                    "account_id": _PAYER,
                    "amount": 1000,
                    "reference": _CREDIT_REFERENCE,
                }
            ),
        },
        "observation": {
            "operation_id": None,
            "observed_kind": "observed_credit",
            "transaction": credit_fact,
            "observed_fact_sha256": _hash(credit_fact),
        },
        "current_escrow": None,
    }

    lock_operation = "op-00000000-0000-4000-8000-000000000502"
    lock = await _lock(client, agent_keypair[0], lock_operation, 100, "task-open")
    assert lock.status_code == 201, lock.text
    lock_body = lock.json()
    assert lock_body["outcome"] == "legacy_observation"
    assert lock_body["attempt"]["operation_id"] == lock_operation
    assert lock_body["attempt"]["operation_kind"] == "escrow_lock"
    assert lock_body["observation"]["operation_id"] is None
    assert lock_body["observation"]["observed_kind"] == "observed_escrow_lock"
    assert lock_body["observation"]["observed_status"] == "locked"
    assert lock_body["observation"]["resolution_fact"] is None
    assert lock_body["observation"]["resolution_fact_sha256"] is None
    assert lock_body["current_escrow"]["status"] == "locked"
    assert _snapshot(gateway_db_path) == before
    assert not before["bank_operation_receipts"]
    assert not before["bank_operation_legs"]


@pytest.mark.parametrize(
    ("kind", "expected_keys"),
    [
        ("credit", {"winning_operation_id", "observed_kind", "tx_id", "observed_fact_sha256"}),
        (
            "lock",
            {
                "winning_operation_id",
                "observed_kind",
                "tx_id",
                "observed_fact_sha256",
                "escrow_id",
                "observed_status",
                "lock_fact_sha256",
                "resolution_fact_sha256",
            },
        ),
    ],
)
async def test_changed_historical_amount_returns_exact_conflict_without_effects(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    kind: str,
    expected_keys: set[str],
) -> None:
    before = _snapshot(gateway_db_path)
    if kind == "credit":
        response = await _credit(
            client,
            platform_keypair[0],
            "op-00000000-0000-4000-8000-000000000503",
            999,
        )
    else:
        response = await _lock(
            client,
            agent_keypair[0],
            "op-00000000-0000-4000-8000-000000000504",
            99,
            "task-open",
        )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "legacy_operation_conflict"
    assert set(body["details"]) == expected_keys
    assert body["details"]["winning_operation_id"] is None
    assert _snapshot(gateway_db_path) == before


@pytest.mark.parametrize(
    ("escrow_id", "split", "observed_status"),
    [("esc-released", False, "released"), ("esc-split", True, "split")],
)
async def test_terminal_historical_settlement_is_unreplayable_without_effects(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    escrow_id: str,
    split: bool,
    observed_status: str,
) -> None:
    before = _snapshot(gateway_db_path)
    operation_id = (
        "op-00000000-0000-4000-8000-000000000505"
        if split
        else "op-00000000-0000-4000-8000-000000000506"
    )
    path, token = _settlement_token(platform_keypair[0], operation_id, escrow_id, split=split)
    response = await client.post(path, json={"token": token})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "historical_settlement_unreplayable"
    assert set(body["details"]) == {
        "winning_operation_id",
        "escrow_id",
        "observed_status",
        "lock_fact_sha256",
        "resolution_fact_sha256",
    }
    assert body["details"]["winning_operation_id"] is None
    assert body["details"]["escrow_id"] == escrow_id
    assert body["details"]["observed_status"] == observed_status
    assert _snapshot(gateway_db_path) == before


async def test_native_operation_identity_precedes_historical_classification(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    operation_id = "op-00000000-0000-4000-8000-000000000507"
    native = await _credit(client, platform_keypair[0], operation_id, 25, "native-credit")
    assert native.status_code == 200, native.text
    assert native.json()["outcome"] == "native_receipt"
    before_conflict = _snapshot(gateway_db_path)
    conflict = await _credit(client, platform_keypair[0], operation_id, 1000)
    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": "operation_id_conflict",
        "message": "Operation identity already belongs to a different financial command",
        "details": {"winning_operation_id": operation_id},
    }
    assert _snapshot(gateway_db_path) == before_conflict


async def test_corrupt_marker_returns_typed_failure_without_new_effects(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    connection = sqlite3.connect(gateway_db_path)
    try:
        connection.execute(
            "DELETE FROM bank_legacy_transaction_observations WHERE tx_id = ?",
            ("tx-historical-credit",),
        )
        connection.commit()
    finally:
        connection.close()
    before = _snapshot(gateway_db_path)
    response = await _credit(
        client,
        platform_keypair[0],
        "op-00000000-0000-4000-8000-000000000508",
        1000,
    )
    assert response.status_code == 500
    assert response.json()["error"] == "receipt_ownership_failed"
    assert _snapshot(gateway_db_path) == before


async def test_native_settlement_preserves_cutover_lock_observation_with_fresh_projection(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    agent_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    settlement_id = "op-00000000-0000-4000-8000-000000000509"
    path, token = _settlement_token(platform_keypair[0], settlement_id, "esc-native", split=False)
    settlement = await client.post(path, json={"token": token})
    assert settlement.status_code == 200, settlement.text
    assert settlement.json()["outcome"] == "native_receipt"
    after_settlement = _snapshot(gateway_db_path)

    observation = await _lock(
        client,
        agent_keypair[0],
        "op-00000000-0000-4000-8000-000000000510",
        100,
        "task-native",
    )
    assert observation.status_code == 201, observation.text
    body = observation.json()
    assert body["outcome"] == "legacy_observation"
    assert body["observation"]["observed_status"] == "locked"
    assert body["observation"]["resolution_fact"] is None
    assert body["observation"]["resolution_fact_sha256"] is None
    assert body["current_escrow"]["status"] == "released"
    assert body["current_escrow"]["resolved_at"] != ""
    assert _snapshot(gateway_db_path) == after_settlement
