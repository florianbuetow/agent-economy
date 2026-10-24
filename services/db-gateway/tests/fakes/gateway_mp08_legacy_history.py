"""Disposable, verified V2→V3→V4 worker-settlement histories for MP-08 tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth import public_key_to_b64
from service_auth.financial_identity import operation_id_for
from service_auth.signing import generate_keypair, keypair_fingerprint
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

from db_gateway_service import migration, recovery
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    from pathlib import Path

LegacySettlementKind = Literal["pending", "succeeded"]
_V2HistoryKind = Literal["pending", "succeeded", "court"]


@dataclass(frozen=True, slots=True)
class LegacyWorkerSettlementV4:
    database: Path
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    reward: int
    accepted_bid_id: str
    accepted_bid_amount: int
    operation_id: str
    observation_id: str
    review_cutoff_us: int
    inventory: RecoveryInventoryV1
    backup_root: Path
    work_root: Path


@dataclass(frozen=True, slots=True)
class ExcludedLegacyHistoryV4:
    database: Path
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    reward: int
    accepted_bid_id: str
    accepted_bid_amount: int
    observation_id: str | None
    observation_kind: str | None
    external_reference: str | None
    review_cutoff_us: int
    inventory: RecoveryInventoryV1
    backup_root: Path
    work_root: Path


@dataclass(slots=True)
class _ConnectionOwner:
    connection: sqlite3.Connection


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _event(
    *,
    source: str,
    event_type: str,
    timestamp: str,
    task_id: str | None,
    agent_id: str | None,
    summary: str,
) -> dict[str, object]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": timestamp,
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": summary,
        "payload": "{}",
    }


def _inventory(root: Path, *, caller_kid: str, database: Path) -> RecoveryInventoryV1:
    source = root / "source"
    db_cwd = source / "db-gateway"
    board_cwd = source / "task-board"
    assets = source / "assets"
    keys = source / "keys"
    configs = source / "configs"
    for directory in (db_cwd, board_cwd, assets, keys, configs, source / "agents"):
        directory.mkdir(parents=True, exist_ok=True)
    generate_keypair(caller_kid, keys)
    db_config_path = configs / "db-gateway.json"
    db_config_bytes = json.dumps(
        {
            "database": {"path": "../data/economy.db"},
            "recovery": {"maintenance_lock_path": "../maintenance.lock"},
            "auth": {
                "trusted_callers": {
                    caller_kid: {
                        "public_key_path": f"../keys/{caller_kid}.pub",
                        "capabilities": ["gateway.board.read", "gateway.board.write"],
                    }
                }
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _write(db_config_path, db_config_bytes, mode=0o640)
    board_config_path = configs / "task-board.json"
    board_config_bytes = json.dumps(
        {
            "db_gateway": {
                "signing": {
                    "kid": caller_kid,
                    "private_key_path": f"../keys/{caller_kid}.key",
                }
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    _write(board_config_path, board_config_bytes, mode=0o640)
    maintenance_lock = source / "maintenance.lock"
    with hold_maintenance_lock(
        maintenance_lock,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass
    lock_metadata = maintenance_lock.lstat()
    resources = (
        SourceResourceV1(
            logical_id="database.primary",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            captured_path=database,
            capture_root=database.parent,
        ),
        SourceResourceV1(
            logical_id="assets.root",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            captured_path=assets,
            capture_root=assets,
        ),
        SourceResourceV1(
            logical_id="config.db-gateway",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            captured_path=db_config_path,
            capture_root=configs,
        ),
        SourceResourceV1(
            logical_id="config.task-board",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            captured_path=board_config_path,
            capture_root=configs,
        ),
        SourceResourceV1(
            logical_id="runtime.maintenance_lock",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.RUNTIME_LOCK,
            captured_path=maintenance_lock,
            capture_root=source,
        ),
        SourceResourceV1(
            logical_id=f"key.{caller_kid}.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / f"{caller_kid}.key",
            capture_root=keys,
        ),
        SourceResourceV1(
            logical_id=f"key.{caller_kid}.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / f"{caller_kid}.pub",
            capture_root=keys,
        ),
    )
    source_configs = (
        SourceConfigV1(
            logical_id="db-gateway",
            captured_path=db_config_path,
            capture_root=configs,
            process_cwd=db_cwd,
            sha256=_digest(db_config_bytes),
        ),
        SourceConfigV1(
            logical_id="task-board",
            captured_path=board_config_path,
            capture_root=configs,
            process_cwd=board_cwd,
            sha256=_digest(board_config_bytes),
        ),
    )
    bindings = (
        SourceFieldBindingV1(
            binding_id="db-gateway.database.path",
            config_id="db-gateway",
            field_path=("database", "path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value="../data/economy.db",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=database,
            resource_id="database.primary",
        ),
        SourceFieldBindingV1(
            binding_id="db-gateway.recovery.maintenance_lock_path",
            config_id="db-gateway",
            field_path=("recovery", "maintenance_lock_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value="../maintenance.lock",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=maintenance_lock,
            resource_id="runtime.maintenance_lock",
        ),
        SourceFieldBindingV1(
            binding_id=f"db-gateway.auth.{caller_kid}.public_key_path",
            config_id="db-gateway",
            field_path=("auth", "trusted_callers", caller_kid, "public_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=f"../keys/{caller_kid}.pub",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=keys / f"{caller_kid}.pub",
            resource_id=f"key.{caller_kid}.public",
        ),
        SourceFieldBindingV1(
            binding_id="task-board.db_gateway.signing.private_key_path",
            config_id="task-board",
            field_path=("db_gateway", "signing", "private_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=f"../keys/{caller_kid}.key",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=keys / f"{caller_kid}.key",
            resource_id=f"key.{caller_kid}.private",
        ),
    )
    keypair = SourceKeypairV1(
        handle=caller_kid,
        role=KeypairRole.GATEWAY,
        private_resource_id=f"key.{caller_kid}.private",
        public_resource_id=f"key.{caller_kid}.public",
        expected_public_fingerprint_sha256=keypair_fingerprint(
            caller_kid,
            keys,
            allow_legacy_private_mode=False,
        ),
        registration_requirement=RegistrationRequirement.FORBIDDEN,
        captured_registration_state=CapturedRegistrationState.UNREGISTERED,
        expected_registered_agent_id=None,
    )
    processes = tuple(
        OwnedProcessV1(
            logical_id=logical_id,
            config_id=logical_id,
            process_cwd=process_cwd,
            agents_package_root=None,
            pid=None,
            pgid=None,
            sid=None,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        )
        for logical_id, process_cwd in (("db-gateway", db_cwd), ("task-board", board_cwd))
    )
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id=f"mp08-legacy-{uuid.uuid4()}",
        source_root=source,
        agents_package_root=source / "agents",
        maintenance_lock_path=maintenance_lock,
        maintenance_lock_identity=MaintenanceLockIdentityV1(
            device=lock_metadata.st_dev,
            inode=lock_metadata.st_ino,
        ),
        resources=resources,
        configs=source_configs,
        field_bindings=bindings,
        keypairs=(keypair,),
        process_ownership=processes,
    )


def _build_v2(database: Path, *, kind: _V2HistoryKind) -> tuple[str, ...]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V2))
    writer = BankWriter(_ConnectionOwner(connection))  # type: ignore[arg-type]
    task_id = f"t-{uuid.uuid4()}"
    poster_id = f"a-mp08-legacy-poster-{uuid.uuid4()}"
    worker_id = f"a-mp08-legacy-worker-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"
    reward = 100
    bid_amount = 90
    operation_id = operation_id_for("task-settlement", (task_id, "approved"))
    created_at = "2099-01-01T00:00:00Z"
    accepted_at = "2099-01-01T00:00:01Z"
    submitted_at = "2099-01-01T00:00:02Z"
    review_deadline = "2099-01-02T00:00:02Z"
    try:
        for agent_id in (poster_id, worker_id):
            public_key = "ed25519:" + public_key_to_b64(Ed25519PrivateKey.generate().public_key())
            connection.execute(
                "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) "
                "VALUES (?,?,?,?)",
                (agent_id, agent_id, public_key, created_at),
            )
        connection.commit()
        writer.create_account(
            {
                "operation_id": f"op-{uuid.uuid4()}",
                "account_id": poster_id,
                "balance": reward,
                "created_at": created_at,
                "initial_credit": {
                    "tx_id": f"tx-{uuid.uuid4()}",
                    "amount": reward,
                    "reference": "initial_balance",
                    "timestamp": created_at,
                },
                "event": _event(
                    source="central-bank",
                    event_type="account.created",
                    timestamp=created_at,
                    task_id=None,
                    agent_id=poster_id,
                    summary="Created funded legacy poster",
                ),
            }
        )
        writer.create_account(
            {
                "account_id": worker_id,
                "balance": 0,
                "created_at": created_at,
                "event": _event(
                    source="central-bank",
                    event_type="account.created",
                    timestamp=created_at,
                    task_id=None,
                    agent_id=worker_id,
                    summary="Created legacy worker",
                ),
            }
        )
        writer.escrow_lock(
            {
                "operation_id": f"op-{uuid.uuid4()}",
                "escrow_id": escrow_id,
                "payer_account_id": poster_id,
                "amount": reward,
                "task_id": task_id,
                "created_at": created_at,
                "tx_id": f"tx-{uuid.uuid4()}",
                "event": _event(
                    source="central-bank",
                    event_type="escrow.locked",
                    timestamp=created_at,
                    task_id=task_id,
                    agent_id=poster_id,
                    summary="Locked legacy task escrow",
                ),
            }
        )
        status = "approved" if kind == "pending" else "submitted"
        connection.execute(
            "INSERT INTO board_tasks "
            "(task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
            "deadline_seconds,review_deadline_seconds,bidding_deadline,execution_deadline,"
            "review_deadline,bid_count,escrow_pending,escrow_id,worker_id,accepted_bid_id,"
            "created_at,accepted_at,submitted_at,approved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                poster_id,
                "Migrated MP-08 settlement",
                "Recover the exact historical worker settlement.",
                reward,
                status,
                3600,
                3600,
                86400,
                "2099-01-01T01:00:00Z",
                "2099-01-01T01:00:01Z",
                review_deadline,
                1,
                1 if kind == "pending" else 0,
                escrow_id,
                worker_id,
                bid_id,
                created_at,
                accepted_at,
                submitted_at,
                submitted_at if kind == "pending" else None,
            ),
        )
        connection.execute(
            "INSERT INTO board_bids (bid_id,task_id,bidder_id,proposal,amount,submitted_at) "
            "VALUES (?,?,?,?,?,?)",
            (bid_id, task_id, worker_id, "Complete the legacy task", bid_amount, accepted_at),
        )
        if kind == "court":
            connection.execute(
                "INSERT INTO court_claims "
                "(claim_id,task_id,claimant_id,respondent_id,reason,status,"
                "rebuttal_deadline,filed_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"clm-{uuid.uuid4()}",
                    task_id,
                    poster_id,
                    worker_id,
                    "The submitted work does not meet the specification",
                    "rebuttal_pending",
                    "2099-01-01T00:10:03Z",
                    "2099-01-01T00:00:03Z",
                ),
            )
        connection.commit()
        if kind == "succeeded":
            writer.escrow_release(
                {
                    "operation_id": operation_id,
                    "escrow_id": escrow_id,
                    "recipient_account_id": worker_id,
                    "source_reference": f"task:{task_id}:approved",
                    "tx_id": f"tx-{uuid.uuid4()}",
                    "resolved_at": "2099-01-01T00:00:03Z",
                    "event": _event(
                        source="central-bank",
                        event_type="escrow.released",
                        timestamp="2099-01-01T00:00:03Z",
                        task_id=task_id,
                        agent_id=worker_id,
                        summary="Released legacy worker settlement",
                    ),
                }
            )
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    return (
        task_id,
        poster_id,
        worker_id,
        escrow_id,
        bid_id,
        str(reward),
        str(bid_amount),
        operation_id,
        review_deadline,
    )


def _build_historical_approved_v1(database: Path) -> tuple[str, ...]:
    """Create an approved worker payment before immutable Bank receipts existed."""
    connection = sqlite3.connect(database)
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    task_id = f"t-{uuid.uuid4()}"
    poster_id = f"a-mp08-historical-poster-{uuid.uuid4()}"
    worker_id = f"a-mp08-historical-worker-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"
    bid_id = f"bid-{uuid.uuid4()}"
    reward = 100
    bid_amount = 90
    created_at = "2099-01-01T00:00:00Z"
    accepted_at = "2099-01-01T00:00:01Z"
    submitted_at = "2099-01-01T00:00:02Z"
    settled_at = "2099-01-01T00:00:03Z"
    review_deadline = "2099-01-02T00:00:02Z"
    try:
        for agent_id in (poster_id, worker_id):
            public_key = "ed25519:" + public_key_to_b64(Ed25519PrivateKey.generate().public_key())
            connection.execute(
                "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) "
                "VALUES (?,?,?,?)",
                (agent_id, agent_id, public_key, created_at),
            )
        event_ids: dict[str, int] = {}
        for key, event_type, timestamp, agent_id in (
            ("credit", "account.created", created_at, poster_id),
            ("lock", "escrow.locked", created_at, poster_id),
            ("release", "escrow.released", settled_at, worker_id),
        ):
            cursor = connection.execute(
                "INSERT INTO events "
                "(event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
                "VALUES ('central-bank',?,?,?,?,?, '{}')",
                (event_type, timestamp, task_id, agent_id, f"Historical {event_type}"),
            )
            event_ids[key] = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES (?,?,?)",
            (poster_id, 0, created_at),
        )
        connection.execute(
            "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES (?,?,?)",
            (worker_id, reward, created_at),
        )
        connection.executemany(
            "INSERT INTO bank_transactions "
            "(tx_id,account_id,type,amount,balance_after,reference,timestamp,event_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    f"tx-{uuid.uuid4()}",
                    poster_id,
                    "credit",
                    reward,
                    reward,
                    "initial_balance",
                    created_at,
                    event_ids["credit"],
                ),
                (
                    f"tx-{uuid.uuid4()}",
                    poster_id,
                    "escrow_lock",
                    reward,
                    0,
                    task_id,
                    created_at,
                    event_ids["lock"],
                ),
                (
                    f"tx-{uuid.uuid4()}",
                    worker_id,
                    "escrow_release",
                    reward,
                    reward,
                    escrow_id,
                    settled_at,
                    event_ids["release"],
                ),
            ),
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id,payer_account_id,amount,task_id,status,created_at,resolved_at,event_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                escrow_id,
                poster_id,
                reward,
                task_id,
                "released",
                created_at,
                settled_at,
                event_ids["lock"],
            ),
        )
        connection.execute(
            "INSERT INTO board_tasks "
            "(task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
            "deadline_seconds,review_deadline_seconds,bidding_deadline,execution_deadline,"
            "review_deadline,bid_count,escrow_pending,escrow_id,worker_id,accepted_bid_id,"
            "created_at,accepted_at,submitted_at,approved_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                poster_id,
                "Historical approved worker payment",
                "Preserve a pre-receipt worker settlement without replay.",
                reward,
                "approved",
                3600,
                3600,
                86400,
                "2099-01-01T01:00:00Z",
                "2099-01-01T01:00:01Z",
                review_deadline,
                1,
                0,
                escrow_id,
                worker_id,
                bid_id,
                created_at,
                accepted_at,
                submitted_at,
                settled_at,
            ),
        )
        connection.execute(
            "INSERT INTO board_bids (bid_id,task_id,bidder_id,proposal,amount,submitted_at) "
            "VALUES (?,?,?,?,?,?)",
            (bid_id, task_id, worker_id, "Historical completed work", bid_amount, accepted_at),
        )
        connection.commit()
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
        assert audit_money(connection).financially_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    return (
        task_id,
        poster_id,
        worker_id,
        escrow_id,
        bid_id,
        str(reward),
        str(bid_amount),
        review_deadline,
    )


def _migrate_v2_source_to_v4(
    *,
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    work_root: Path,
    database: Path,
    task_id: str,
    caller_kid: str,
) -> tuple[int, tuple[tuple[object, ...], ...]]:
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-v3",
        work_root=work_root,
    )
    v3 = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id="before-v3",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v3.observation == "committed_v3_verified"
    connection = sqlite3.connect(database)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V3
        contract = connection.execute(
            "SELECT lifecycle_version,review_cutoff_us FROM board_task_contracts WHERE task_id=?",
            (task_id,),
        ).fetchone()
        assert contract is not None and contract[0] == 0
        observations = tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT observation_id,observation_kind,external_reference,unresolved "
                "FROM board_legacy_effect_observations WHERE task_id=? "
                "ORDER BY observation_id",
                (task_id,),
            ).fetchall()
        )
        review_cutoff_us = int(contract[1])
    finally:
        connection.close()
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-v4",
        work_root=work_root,
    )
    v4 = migration.migrate_v3_to_v4(
        inventory,
        backup_root=backup_root,
        backup_id="before-v4",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v4.observation == "committed_v4_verified"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V4
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert audit_task_contracts(connection).contracts_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        cutover = connection.execute(
            "SELECT cutover_version,source_schema_version,caller_kid FROM board_publication_cutover"
        ).fetchone()
        assert cutover == (1, 3, caller_kid)
    finally:
        connection.close()
    return review_cutoff_us, observations


def prepare_legacy_worker_settlement_v4(
    root: Path,
    *,
    kind: LegacySettlementKind,
    caller_kid: str,
) -> LegacyWorkerSettlementV4:
    """Create one authentic derived observation through both verified migrations."""
    if kind not in {"pending", "succeeded"}:
        raise ValueError("kind must be pending or succeeded")
    root = root.resolve()
    source_root = root / "source"
    database = source_root / "data" / "economy.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    (
        task_id,
        poster_id,
        worker_id,
        escrow_id,
        bid_id,
        reward_text,
        bid_amount_text,
        operation_id,
        review_deadline,
    ) = _build_v2(database, kind=kind)
    inventory = _inventory(root, caller_kid=caller_kid, database=database)
    backup_root = root / "backups"
    work_root = root / "work"
    backup_root.mkdir(mode=0o700)
    work_root.mkdir(mode=0o700)
    review_cutoff_us, observations = _migrate_v2_source_to_v4(
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
        database=database,
        task_id=task_id,
        caller_kid=caller_kid,
    )
    expected_kind = f"worker_settlement_{kind}"
    assert observations == ((observations[0][0], expected_kind, operation_id, 1),)
    assert review_cutoff_us == parse_utc_text_to_epoch_us(review_deadline)
    return LegacyWorkerSettlementV4(
        database=database,
        task_id=task_id,
        poster_id=poster_id,
        worker_id=worker_id,
        escrow_id=escrow_id,
        reward=int(reward_text),
        accepted_bid_id=bid_id,
        accepted_bid_amount=int(bid_amount_text),
        operation_id=operation_id,
        observation_id=str(observations[0][0]),
        review_cutoff_us=review_cutoff_us,
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
    )


def prepare_historical_approved_worker_payment_v4(
    root: Path,
    *,
    caller_kid: str,
) -> ExcludedLegacyHistoryV4:
    """Migrate a paid approved task whose Bank evidence predates native receipts."""
    root = root.resolve()
    database = root / "source/data/economy.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    (
        task_id,
        poster_id,
        worker_id,
        escrow_id,
        bid_id,
        reward_text,
        bid_amount_text,
        review_deadline,
    ) = _build_historical_approved_v1(database)
    inventory = _inventory(root, caller_kid=caller_kid, database=database)
    backup_root = root / "backups"
    work_root = root / "work"
    backup_root.mkdir(mode=0o700)
    work_root.mkdir(mode=0o700)
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="before-v2",
        work_root=work_root,
    )
    v2 = migration.migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="before-v2",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v2.observation == "committed_v2_verified"
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert connection.execute("SELECT COUNT(*) FROM bank_operation_receipts").fetchone() == (0,)
        assert connection.execute(
            "SELECT observed_kind FROM bank_legacy_transaction_observations ORDER BY observed_kind"
        ).fetchall() == [
            ("observed_credit",),
            ("observed_escrow_lock",),
            ("observed_escrow_release",),
        ]
        assert connection.execute(
            "SELECT observed_status FROM bank_legacy_escrow_observations WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone() == ("released",)
    finally:
        connection.close()
    review_cutoff_us, observations = _migrate_v2_source_to_v4(
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
        database=database,
        task_id=task_id,
        caller_kid=caller_kid,
    )
    assert observations == ()
    assert review_cutoff_us == parse_utc_text_to_epoch_us(review_deadline)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM bank_operation_receipts").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_legacy_effect_observations WHERE task_id=?",
            (task_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()
    return ExcludedLegacyHistoryV4(
        database=database,
        task_id=task_id,
        poster_id=poster_id,
        worker_id=worker_id,
        escrow_id=escrow_id,
        reward=int(reward_text),
        accepted_bid_id=bid_id,
        accepted_bid_amount=int(bid_amount_text),
        observation_id=None,
        observation_kind=None,
        external_reference=None,
        review_cutoff_us=review_cutoff_us,
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
    )


def prepare_legacy_court_claim_v4(
    root: Path,
    *,
    caller_kid: str,
) -> ExcludedLegacyHistoryV4:
    """Migrate an authentic unlinked Court claim without creating financial work."""
    root = root.resolve()
    database = root / "source/data/economy.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    (
        task_id,
        poster_id,
        worker_id,
        escrow_id,
        bid_id,
        reward_text,
        bid_amount_text,
        _unused_operation_id,
        review_deadline,
    ) = _build_v2(database, kind="court")
    inventory = _inventory(root, caller_kid=caller_kid, database=database)
    backup_root = root / "backups"
    work_root = root / "work"
    backup_root.mkdir(mode=0o700)
    work_root.mkdir(mode=0o700)
    review_cutoff_us, observations = _migrate_v2_source_to_v4(
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
        database=database,
        task_id=task_id,
        caller_kid=caller_kid,
    )
    assert len(observations) == 1
    observation_id, observation_kind, external_reference, unresolved = observations[0]
    assert observation_kind == "court_claim_succeeded"
    assert unresolved == 1
    assert review_cutoff_us == parse_utc_text_to_epoch_us(review_deadline)
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts "
            "WHERE escrow_id=? AND operation_kind IN ('escrow_release','escrow_split')",
            (escrow_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM board_legacy_effect_observations "
            "WHERE task_id=? AND observation_kind LIKE 'worker_settlement_%'",
            (task_id,),
        ).fetchone() == (0,)
    finally:
        connection.close()
    return ExcludedLegacyHistoryV4(
        database=database,
        task_id=task_id,
        poster_id=poster_id,
        worker_id=worker_id,
        escrow_id=escrow_id,
        reward=int(reward_text),
        accepted_bid_id=bid_id,
        accepted_bid_amount=int(bid_amount_text),
        observation_id=str(observation_id),
        observation_kind=str(observation_kind),
        external_reference=str(external_reference),
        review_cutoff_us=review_cutoff_us,
        inventory=inventory,
        backup_root=backup_root,
        work_root=work_root,
    )


__all__ = [
    "ExcludedLegacyHistoryV4",
    "LegacySettlementKind",
    "LegacyWorkerSettlementV4",
    "prepare_historical_approved_worker_payment_v4",
    "prepare_legacy_court_claim_v4",
    "prepare_legacy_worker_settlement_v4",
]
