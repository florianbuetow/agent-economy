"""Receipt-backed v3 histories for MP-05 publication migration tests."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.financial_identity import operation_id_for
from service_auth.signing import generate_keypair, keypair_fingerprint
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
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
from db_gateway_service.task_time import (
    add_seconds_to_epoch_us,
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)
from tests.fakes.historical_v3_writer import (
    HistoricalV3Writer,
    open_historical_v3_writer,
)
from tests.unit import test_mp06_migration as v0_migration_tests
from tests.unit import test_mp07_migration_contract as v3_migration_tests

if TYPE_CHECKING:
    from pathlib import Path

    from db_gateway_service.services.db_writer import DbWriter


_CREATED_AT = "2099-01-01T12:00:00.000000Z"
_CREATED_AT_US = parse_utc_text_to_epoch_us(_CREATED_AT)
_DEFAULT_CALLER_KID = "gateway-task-board"
LEGACY_REFUNDED_ESCROW_ID = "esc-bank-only"


@dataclass(frozen=True, slots=True)
class V3Source:
    """One verified v3 source and its existing backup/work roots."""

    inventory: RecoveryInventoryV1
    backup_root: Path
    work_root: Path
    database: Path
    caller_kid: str


@dataclass(frozen=True, slots=True)
class LockedEscrow:
    """One real receipt-backed lock created by the production writer."""

    escrow_id: str
    payer_id: str
    task_id: str
    amount: int
    operation_id: str
    receipt: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RichCutoverHistory:
    """Published winner plus pending and already-refunded posting orphans."""

    task_id: str
    winner: LockedEscrow
    pending_orphans: tuple[LockedEscrow, ...]
    refunded_orphan: LockedEscrow
    existing_refund_operation_id: str
    existing_refund_source_reference: str


def prepare_v3_source(
    tmp_path: Path,
    *,
    caller_kid: str = _DEFAULT_CALLER_KID,
) -> V3Source:
    """Create an exact verified v3 source through the approved migration chain."""
    inventory, backup_root, work_root, database = v3_migration_tests._prepared_v2(tmp_path)
    result = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp07",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.applied is True
    assert result.schema_version == 3
    assert result.observation == "committed_v3_verified"
    return V3Source(
        _with_task_board_authority(inventory, handle=caller_kid),
        backup_root,
        work_root,
        database,
        caller_kid,
    )


def prepare_v3_source_with_legacy_refund(
    tmp_path: Path,
    *,
    caller_kid: str = _DEFAULT_CALLER_KID,
) -> V3Source:
    """Build a released legacy orphan through the real v0→v1→v2→v3 chain."""
    inventory, backup_root, work_root, database = v0_migration_tests._prepared_v0(tmp_path)
    connection = sqlite3.connect(database)
    try:
        _repair_legacy_task_timing(connection)
        released_at = "2026-10-17T12:00:04.000000Z"
        event = connection.execute(
            "INSERT INTO events "
            "(event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES ('bank', 'escrow.released', ?, 't-bank-only', 'a-external', "
            "'legacy posting refund', '{}')",
            (released_at,),
        )
        event_id = int(event.lastrowid)
        connection.execute("UPDATE bank_accounts SET balance=100 WHERE account_id='a-external'")
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id, account_id, type, amount, balance_after, reference, timestamp, event_id) "
            "VALUES ('tx-release-bank-only', 'a-external', 'escrow_release', 50, 100, "
            "?, ?, ?)",
            (LEGACY_REFUNDED_ESCROW_ID, released_at, event_id),
        )
        connection.execute(
            "UPDATE bank_escrow SET status='released', resolved_at=? WHERE escrow_id=?",
            (released_at, LEGACY_REFUNDED_ESCROW_ID),
        )
        connection.commit()
    finally:
        connection.close()

    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp06",
        work_root=work_root,
    )
    v1 = migration.migrate_v0_to_v1(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp06",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v1.observation == "committed_v1_verified"
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp04",
        work_root=work_root,
    )
    v2 = migration.migrate_v1_to_v2(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp04",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v2.observation == "committed_v2_verified"
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp07",
        work_root=work_root,
    )
    v3 = migration.migrate_v2_to_v3(
        inventory,
        backup_root=backup_root,
        backup_id="legacy-before-mp07",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert v3.observation == "committed_v3_verified"
    return V3Source(
        _with_task_board_authority(inventory, handle=caller_kid),
        backup_root,
        work_root,
        database,
        caller_kid,
    )


def invalidate_posting_authority(source: V3Source, *, defect: str) -> V3Source:
    """Create one named backup-bound authority defect without touching keys."""
    configs = {config.logical_id: config for config in source.inventory.configs}
    bindings = source.inventory.field_bindings
    if defect == "missing_board_write":
        gateway_path = configs["gateway"].captured_path
        value = json.loads(gateway_path.read_text(encoding="utf-8"))
        value["auth"]["trusted_callers"][source.caller_kid]["capabilities"] = ["gateway.board.read"]
        gateway_path.write_text(_canonical_json(value), encoding="utf-8")
    elif defect == "caller_mismatch":
        gateway_path = configs["gateway"].captured_path
        value = json.loads(gateway_path.read_text(encoding="utf-8"))
        del value["auth"]["trusted_callers"][source.caller_kid]
        gateway_path.write_text(_canonical_json(value), encoding="utf-8")
        bindings = tuple(
            binding
            for binding in bindings
            if binding.binding_id != f"gateway.auth.{source.caller_kid}.public_key_path"
        )
    else:
        raise ValueError(f"Unknown posting authority defect: {defect}")
    return V3Source(
        _rehash_inventory_configs(source.inventory, field_bindings=bindings),
        source.backup_root,
        source.work_root,
        source.database,
        source.caller_kid,
    )


def open_v3_writer(source: V3Source) -> HistoricalV3Writer:
    """Open the exact-v3 test adapter with real Identity and Bank writers."""
    return open_historical_v3_writer(
        str(source.database),
        busy_timeout_ms=5000,
        journal_mode="wal",
    )


def add_rich_cutover_history(writer: DbWriter) -> RichCutoverHistory:
    """Create positive publication-cutover history only through real writers."""
    task_id = f"t-{uuid4()}"
    winner_payer = create_funded_payer(writer, name="Published winner")
    winner = lock_escrow(writer, payer_id=winner_payer, task_id=task_id, amount=100)
    publish_legacy_task(writer, winner)

    pending = tuple(
        lock_escrow(
            writer,
            payer_id=create_funded_payer(writer, name=f"Pending orphan {index}"),
            task_id=task_id,
            amount=100,
        )
        for index in range(2)
    )

    refunded_task_id = f"t-{uuid4()}"
    refunded_payer = create_funded_payer(writer, name="Already-refunded orphan")
    refunded = lock_escrow(
        writer,
        payer_id=refunded_payer,
        task_id=refunded_task_id,
        amount=75,
    )
    old_operation_id = operation_id_for(
        "task-settlement",
        (refunded_task_id, "posting_compensation"),
    )
    old_source = f"task:{refunded_task_id}:posting_compensation"
    release_escrow(
        writer,
        refunded,
        recipient_id=refunded_payer,
        operation_id=old_operation_id,
        source_reference=old_source,
    )
    return RichCutoverHistory(
        task_id=task_id,
        winner=winner,
        pending_orphans=pending,
        refunded_orphan=refunded,
        existing_refund_operation_id=old_operation_id,
        existing_refund_source_reference=old_source,
    )


def create_funded_payer(writer: DbWriter, *, name: str, balance: int = 1_000) -> str:
    """Register and fund one payer through receipt-producing domain writers."""
    agent_id = f"a-{uuid4()}"
    public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": name,
            "public_key": "ed25519:" + base64.b64encode(public_key).decode("ascii"),
            "registered_at": _CREATED_AT,
            "event": _event("identity", "agent.registered", agent_id=agent_id),
        }
    )
    operation_id = f"op-{uuid4()}"
    result = writer.create_account(
        {
            "account_id": agent_id,
            "balance": balance,
            "created_at": _CREATED_AT,
            "operation_id": operation_id,
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": _CREATED_AT,
            },
            "event": _event("central-bank", "account.created", agent_id=agent_id),
        }
    )
    assert result["receipt"]["operation_id"] == operation_id
    assert result["receipt"]["operation_kind"] == "account_issuance"
    return agent_id


def lock_escrow(
    writer: DbWriter,
    *,
    payer_id: str,
    task_id: str,
    amount: int,
) -> LockedEscrow:
    """Lock a task reward and retain the authoritative receipt."""
    escrow_id = f"esc-{uuid4()}"
    operation_id = operation_id_for("task-lock", (task_id, payer_id))
    result = writer.escrow_lock(
        {
            "operation_id": operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": _CREATED_AT,
            "tx_id": f"tx-{uuid4()}",
            "event": _event(
                "central-bank",
                "escrow.locked",
                task_id=task_id,
                agent_id=payer_id,
            ),
        }
    )
    receipt = result["receipt"]
    assert receipt["operation_id"] == operation_id
    assert receipt["operation_kind"] == "escrow_lock"
    assert receipt["escrow_id"] == escrow_id
    assert result["current_escrow"]["status"] == "locked"
    return LockedEscrow(escrow_id, payer_id, task_id, amount, operation_id, receipt)


def publish_legacy_task(writer: DbWriter, lock: LockedEscrow) -> None:
    """Publish one pre-v4 task through the real legacy Board writer."""
    bidding_seconds = 3_600
    acceptance_seconds = 1_800
    bidding_cutoff_us = add_seconds_to_epoch_us(_CREATED_AT_US, bidding_seconds)
    result = writer.create_task(
        {
            "task_id": lock.task_id,
            "poster_id": lock.payer_id,
            "title": "Legacy published task",
            "spec": "Preserve these exact terms without inventing publication ownership.",
            "reward": lock.amount,
            "status": "open",
            "bidding_deadline_seconds": bidding_seconds,
            "acceptance_window_seconds": acceptance_seconds,
            "deadline_seconds": 7_200,
            "review_deadline_seconds": 900,
            "bidding_deadline": epoch_us_to_utc_text(bidding_cutoff_us),
            "bidding_cutoff_us": bidding_cutoff_us,
            "acceptance_cutoff_us": add_seconds_to_epoch_us(
                bidding_cutoff_us,
                acceptance_seconds,
            ),
            "escrow_id": lock.escrow_id,
            "created_at": _CREATED_AT,
            "event": _event("task-board", "task.created", task_id=lock.task_id),
        }
    )
    assert result["task_id"] == lock.task_id
    assert isinstance(result["event_id"], int)


def release_escrow(
    writer: DbWriter,
    lock: LockedEscrow,
    *,
    recipient_id: str,
    operation_id: str,
    source_reference: str,
) -> dict[str, Any]:
    """Resolve a lock through the real release writer and return its receipt."""
    result = writer.escrow_release(
        {
            "operation_id": operation_id,
            "escrow_id": lock.escrow_id,
            "recipient_account_id": recipient_id,
            "source_reference": source_reference,
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": _CREATED_AT,
            "event": _event(
                "central-bank",
                "escrow.released",
                task_id=lock.task_id,
                agent_id=recipient_id,
            ),
        }
    )
    assert result["receipt"]["operation_id"] == operation_id
    assert result["receipt"]["operation_kind"] == "escrow_release"
    assert result["current_escrow"]["status"] == "released"
    return result["receipt"]


def split_escrow(
    writer: DbWriter,
    lock: LockedEscrow,
    *,
    worker_id: str,
    worker_pct: int,
) -> dict[str, Any]:
    """Resolve a lock through the real split writer for a rejection history."""
    operation_id = f"op-{uuid4()}"
    result = writer.escrow_split(
        {
            "operation_id": operation_id,
            "escrow_id": lock.escrow_id,
            "worker_account_id": worker_id,
            "poster_account_id": lock.payer_id,
            "worker_pct": worker_pct,
            "source_reference": f"task:{lock.task_id}:historical_split",
            "worker_tx_id": f"tx-{uuid4()}",
            "poster_tx_id": f"tx-{uuid4()}",
            "resolved_at": _CREATED_AT,
            "event": _event(
                "central-bank",
                "escrow.split",
                task_id=lock.task_id,
            ),
        }
    )
    assert result["receipt"]["operation_id"] == operation_id
    assert result["receipt"]["operation_kind"] == "escrow_split"
    assert result["current_escrow"]["status"] == "split"
    return result["receipt"]


def publish_verified_backup(source: V3Source, *, backup_id: str) -> dict[str, object]:
    """Create and independently verify the exact source backup used by migration."""
    publication = recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
    )
    assert publication.state is recovery.PublicationState.COMPLETE
    verification = verify_backup_without_mutating(source, backup_id=backup_id)
    assert verification["artifact_verified"] is True
    assert verification["snapshot_preserved"] is True
    assert verification["identity_complete"] is True
    return verification


def verify_backup_without_mutating(
    source: V3Source,
    *,
    backup_id: str,
) -> dict[str, object]:
    """Run the production verifier on an exact disposable copy of an artifact."""
    verification_root = source.work_root.parent / f".mp05-verification-root-{uuid4()}"
    verification_work = source.work_root.parent / f".mp05-verification-work-{uuid4()}"
    verification_root.mkdir(mode=0o700)
    verification_work.mkdir(mode=0o700)
    shutil.copytree(
        source.backup_root / backup_id,
        verification_root / backup_id,
        copy_function=shutil.copy2,
    )
    return recovery.verify_backup(
        backup_root=verification_root,
        backup_id=backup_id,
        work_root=verification_work,
    ).model_dump(mode="json")


def _event(
    source: str,
    event_type: str,
    *,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, object]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": _CREATED_AT,
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": f"MP-05 migration {event_type}",
        "payload": "{}",
    }


def _with_task_board_authority(
    inventory: RecoveryInventoryV1,
    *,
    handle: str,
) -> RecoveryInventoryV1:
    """Capture the Board signing identity and matching Gateway trust binding."""
    source_root = inventory.source_root
    keys = source_root / "keys"
    generate_keypair(handle, keys)

    gateway_config = next(config for config in inventory.configs if config.logical_id == "gateway")
    gateway_value = json.loads(gateway_config.captured_path.read_text(encoding="utf-8"))
    gateway_value["auth"]["trusted_callers"][handle] = {
        "public_key_path": f"../keys/{handle}.pub",
        "capabilities": ["gateway.board.write"],
    }
    gateway_bytes = _canonical_json(gateway_value).encode("utf-8")
    gateway_config.captured_path.write_bytes(gateway_bytes)

    board_cwd = source_root / "task-board"
    board_cwd.mkdir()
    board_config_path = source_root / "configs/task-board.yaml"
    board_bytes = _canonical_json(
        {
            "db_gateway": {
                "signing": {
                    "kid": handle,
                    "private_key_path": f"../keys/{handle}.key",
                }
            }
        }
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
                    if config.logical_id == "gateway"
                    else config.sha256
                ),
            )
            for config in inventory.configs
        ),
        SourceConfigV1(
            logical_id="task-board",
            captured_path=board_config_path,
            capture_root=board_config_path.parent,
            process_cwd=board_cwd,
            sha256=hashlib.sha256(board_bytes).hexdigest(),
        ),
    )
    resources = (
        *inventory.resources,
        SourceResourceV1(
            logical_id="config.task-board",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            captured_path=board_config_path,
            capture_root=board_config_path.parent,
        ),
        SourceResourceV1(
            logical_id=f"key.{handle}.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / f"{handle}.key",
            capture_root=keys,
        ),
        SourceResourceV1(
            logical_id=f"key.{handle}.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / f"{handle}.pub",
            capture_root=keys,
        ),
    )
    bindings = (
        *inventory.field_bindings,
        SourceFieldBindingV1(
            binding_id=f"gateway.auth.{handle}.public_key_path",
            config_id="gateway",
            field_path=("auth", "trusted_callers", handle, "public_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=f"../keys/{handle}.pub",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=keys / f"{handle}.pub",
            resource_id=f"key.{handle}.public",
        ),
        SourceFieldBindingV1(
            binding_id="task-board.db_gateway.signing.private_key_path",
            config_id="task-board",
            field_path=("db_gateway", "signing", "private_key_path"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value=f"../keys/{handle}.key",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=keys / f"{handle}.key",
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
            process_cwd=board_cwd,
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
        contract_version=1,
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


def _rehash_inventory_configs(
    inventory: RecoveryInventoryV1,
    *,
    field_bindings: tuple[SourceFieldBindingV1, ...],
) -> RecoveryInventoryV1:
    configs = tuple(
        SourceConfigV1(
            logical_id=config.logical_id,
            captured_path=config.captured_path,
            capture_root=config.capture_root,
            process_cwd=config.process_cwd,
            sha256=hashlib.sha256(config.captured_path.read_bytes()).hexdigest(),
        )
        for config in inventory.configs
    )
    return RecoveryInventoryV1(
        contract_version=1,
        runtime_id=inventory.runtime_id,
        source_root=inventory.source_root,
        agents_package_root=inventory.agents_package_root,
        maintenance_lock_path=inventory.maintenance_lock_path,
        maintenance_lock_identity=inventory.maintenance_lock_identity,
        resources=inventory.resources,
        configs=configs,
        field_bindings=field_bindings,
        keypairs=inventory.keypairs,
        process_ownership=inventory.process_ownership,
    )


def _repair_legacy_task_timing(connection: sqlite3.Connection) -> None:
    """Make the inherited v0 task a valid input to the later v2→v3 cutover."""
    connection.execute(
        "UPDATE board_tasks SET created_at='2026-10-17T12:00:00.125000Z', "
        "bidding_deadline='2026-10-17T12:00:01.125000Z', "
        "accepted_bid_id='bid-1', accepted_at='2026-10-17T12:00:00.500000Z', "
        "execution_deadline='2026-10-17T12:00:01.500000Z', "
        "submitted_at='2026-10-17T12:00:01Z', review_deadline='2026-10-17T12:00:02Z', "
        "disputed_at='2026-10-17T12:00:01.500000Z', bid_count=1, "
        "rebuttal_deadline='2026-10-17T12:00:11.500000Z', "
        "rebuttal_submitted_at='2026-10-17T12:00:03Z' WHERE task_id='t-1'"
    )
    connection.execute(
        "UPDATE board_bids SET amount=40, submitted_at='2026-10-17T12:00:00.250000Z'"
    )
    connection.execute(
        "UPDATE court_claims SET status='rebuttal_submitted', "
        "filed_at='2026-10-17T12:00:01.500000Z', "
        "rebuttal_deadline='2026-10-17T12:00:11.500000Z'"
    )
    connection.execute("UPDATE court_rebuttals SET submitted_at='2026-10-17T12:00:03Z'")
    connection.execute(
        "UPDATE board_assets SET uploader_id='a-worker', uploaded_at='2026-10-17T12:00:00.750000Z'"
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


__all__ = [
    "LEGACY_REFUNDED_ESCROW_ID",
    "LockedEscrow",
    "RichCutoverHistory",
    "V3Source",
    "add_rich_cutover_history",
    "create_funded_payer",
    "invalidate_posting_authority",
    "lock_escrow",
    "open_v3_writer",
    "prepare_v3_source",
    "prepare_v3_source_with_legacy_refund",
    "publish_verified_backup",
    "release_escrow",
    "split_escrow",
    "verify_backup_without_mutating",
]
