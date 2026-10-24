"""Explicit verified migration from receipt schema v2 to task-contract schema v3."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from db_gateway_service.migration import (
    _ALLOWED_ARTIFACT_FINDINGS,
    MigrationError,
    MigrationResult,
    _close_read_only,
    _configure_migration_connection,
    _dispose_writer,
    _open_live_writer,
    _open_read_only,
    _rollback_with_note,
    _snapshot_value,
    _split_sql,
    _validate_database_health,
    _validate_timeout,
    _write_evidence,
    _write_evidence_with_note,
)
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.recovery import RecoveryError, open_backup_bound_source
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_time import add_seconds_to_epoch_us, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Never

    from service_commons.recovery_contract import RecoveryInventoryV1

    from db_gateway_service.recovery import BackupBoundSource

_SUPPORTED_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "expired", "disputed", "ruled"}
)


@dataclass(frozen=True, slots=True)
class _TaskContract:
    task_id: str
    lifecycle_version: int
    timing_contract_version: int
    acceptance_window_seconds: None
    bidding_cutoff_us: int
    acceptance_cutoff_us: int
    execution_cutoff_us: int | None
    review_cutoff_us: int | None
    accepted_reward: int | None
    accepted_bid_amount: int | None


@dataclass(frozen=True, slots=True)
class _LegacyObservation:
    observation_id: str
    task_id: str
    observation_kind: str
    observed_fact_json: str
    observed_fact_sha256: str
    external_reference: str
    effect_state: str
    target_outcome_hint: str
    unresolved: int


@dataclass(frozen=True, slots=True)
class _Admission:
    snapshot: dict[str, object]
    metadata: tuple[object, ...]
    contracts: tuple[_TaskContract, ...]
    observations: tuple[_LegacyObservation, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    sidecar_sql: str
    metadata_sql: str
    sha256: str


def migrate_v2_to_v3(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    busy_timeout_ms: int,
) -> MigrationResult:
    """Migrate one verified, quiescent v2 source without implicit chaining."""
    _validate_timeout(busy_timeout_ms)
    plan = _plan()
    try:
        with open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        ) as bound:
            verification = bound.artifact_verification_result
            if (
                not verification.artifact_verified
                or not verification.snapshot_preserved
                or not verification.identity_complete
            ):
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup preservation or identity verification is incomplete",
                    {"finding_codes": list(verification.finding_codes)},
                )
            disallowed = sorted(set(verification.finding_codes) - _ALLOWED_ARTIFACT_FINDINGS)
            if disallowed:
                raise MigrationError(
                    "backup_not_admitted",
                    "Backup has unresolved migration findings",
                    {"finding_codes": disallowed},
                )
            clone = _open_read_only(bound.database_path)
            clone_primary: BaseException | None = None
            try:
                admission = _admit_v2(clone)
            except BaseException as primary:
                clone_primary = primary
                raise
            finally:
                _close_read_only(clone, primary=clone_primary, label="v2 admission clone")
            bound.verify_before_write()
            bound.begin_write_phase()
            return _migrate_bound(
                bound,
                admission=admission,
                plan=plan,
                backup_id=backup_id,
                manifest_sha256=bound.manifest_sha256,
                busy_timeout_ms=busy_timeout_ms,
            )
    except MigrationError:
        raise
    except RecoveryError as exc:
        raise MigrationError(exc.reason, str(exc), {}) from exc


def _admit_v2(connection: sqlite3.Connection) -> _Admission:
    classification = classify_schema(connection)
    if classification.state is not SchemaState.CURRENT_V2:
        raise MigrationError(
            "source_not_v2",
            "Verified backup source is not the exact supported v2 schema",
            {"source_schema": classification.state.value},
        )
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Source money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Source receipt audit failed", {})
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None:
        raise MigrationError("source_not_v2", "Source metadata is missing", {})
    contracts, observations = _derive_task_contracts(connection)
    return _Admission(
        snapshot=_database_snapshot(connection, tables=None),
        metadata=tuple(metadata),
        contracts=contracts,
        observations=observations,
    )


def _derive_task_contracts(
    connection: sqlite3.Connection,
) -> tuple[tuple[_TaskContract, ...], tuple[_LegacyObservation, ...]]:
    tasks = connection.execute(
        "SELECT task_id, poster_id, reward, status, bidding_deadline_seconds, "
        "deadline_seconds, review_deadline_seconds, bidding_deadline, execution_deadline, "
        "review_deadline, escrow_pending, escrow_id, worker_id, accepted_bid_id, "
        "dispute_reason, dispute_id, created_at, accepted_at, submitted_at "
        "FROM board_tasks ORDER BY task_id"
    ).fetchall()
    contracts: list[_TaskContract] = []
    observations: list[_LegacyObservation] = []
    for raw in tasks:
        row = tuple(raw)
        task_id = str(row[0])
        if row[3] not in _SUPPORTED_STATUSES:
            _fail("task_history_unsupported", "Task status is unsupported", task_id)
        bidding_cutoff, execution_cutoff, review_cutoff = _validated_cutoffs(row, task_id)
        accepted_reward, accepted_bid_amount = _validated_accepted_terms(connection, row, task_id)
        _validate_dispute_linkage(connection, row, task_id)
        task_observations = _classify_legacy_effects(
            connection,
            row,
            task_id=task_id,
            accepted_bid_amount=accepted_bid_amount,
        )
        observations.extend(task_observations)
        contracts.append(
            _TaskContract(
                task_id=task_id,
                lifecycle_version=0,
                timing_contract_version=1,
                acceptance_window_seconds=None,
                bidding_cutoff_us=bidding_cutoff,
                acceptance_cutoff_us=bidding_cutoff,
                execution_cutoff_us=execution_cutoff,
                review_cutoff_us=review_cutoff,
                accepted_reward=accepted_reward,
                accepted_bid_amount=accepted_bid_amount,
            )
        )
    return tuple(contracts), tuple(observations)


def _validated_cutoffs(
    task: tuple[object, ...], task_id: str
) -> tuple[int, int | None, int | None]:
    try:
        created_us = parse_utc_text_to_epoch_us(task[16])
        bidding_us = parse_utc_text_to_epoch_us(task[7])
        bidding_seconds = _positive_integer(task[4])
        execution_seconds = _positive_integer(task[5])
        review_seconds = _positive_integer(task[6])
        if bidding_us != add_seconds_to_epoch_us(created_us, bidding_seconds):
            raise ValueError("bidding cutoff mismatch")
        accepted_us = None if task[17] is None else parse_utc_text_to_epoch_us(task[17])
        execution_us = None if task[8] is None else parse_utc_text_to_epoch_us(task[8])
        submitted_us = None if task[18] is None else parse_utc_text_to_epoch_us(task[18])
        review_us = None if task[9] is None else parse_utc_text_to_epoch_us(task[9])
        if (accepted_us is None) != (execution_us is None):
            raise ValueError("execution cutoff presence mismatch")
        if accepted_us is not None and execution_us != add_seconds_to_epoch_us(
            accepted_us, execution_seconds
        ):
            raise ValueError("execution cutoff mismatch")
        if (submitted_us is None) != (review_us is None):
            raise ValueError("review cutoff presence mismatch")
        if submitted_us is not None and review_us != add_seconds_to_epoch_us(
            submitted_us, review_seconds
        ):
            raise ValueError("review cutoff mismatch")
    except ValueError:
        _fail("task_cutoff_inconsistent", "Task deadline history is inconsistent", task_id)
    return bidding_us, execution_us, review_us


def _positive_integer(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("duration must be a positive integer")
    return value


def _validated_accepted_terms(
    connection: sqlite3.Connection,
    task: tuple[object, ...],
    task_id: str,
) -> tuple[int | None, int | None]:
    worker_id, accepted_bid_id = task[12], task[13]
    accepted_at, execution_deadline = task[17], task[8]
    requires_acceptance = task[3] in {"accepted", "submitted", "approved", "disputed", "ruled"}
    if worker_id is None and accepted_bid_id is None:
        if requires_acceptance or accepted_at is not None or execution_deadline is not None:
            _fail(
                "accepted_bid_linkage_invalid",
                "Task has accepted timing without accepted bid terms",
                task_id,
            )
        return None, None
    if worker_id is None or accepted_bid_id is None or accepted_at is None:
        _fail("accepted_bid_linkage_invalid", "Accepted task terms are incomplete", task_id)
    if task[3] == "open":
        _fail("accepted_bid_linkage_invalid", "Open task has accepted bid terms", task_id)
    bid = connection.execute(
        "SELECT bidder_id, amount FROM board_bids WHERE bid_id=? AND task_id=?",
        (accepted_bid_id, task_id),
    ).fetchone()
    if bid is None or bid[0] != worker_id or type(bid[1]) is not int or bid[1] < 0:
        _fail(
            "accepted_bid_linkage_invalid",
            "Accepted bid does not match the task worker",
            task_id,
        )
    if type(task[2]) is not int or task[2] <= 0:
        _fail("accepted_bid_linkage_invalid", "Accepted reward is invalid", task_id)
    return int(task[2]), int(bid[1])


def _validate_dispute_linkage(
    connection: sqlite3.Connection,
    task: tuple[object, ...],
    task_id: str,
) -> None:
    dispute_id = task[15]
    if dispute_id is None:
        if task[3] in {"disputed", "ruled"}:
            _fail(
                "dispute_claim_linkage_invalid",
                "Disputed task has no Court claim linkage",
                task_id,
            )
        return
    if task[3] not in {"disputed", "ruled"}:
        _fail(
            "dispute_claim_linkage_invalid",
            "Task links a Court claim outside a dispute state",
            task_id,
        )
    claim = connection.execute(
        "SELECT task_id, claimant_id, respondent_id, reason FROM court_claims WHERE claim_id=?",
        (dispute_id,),
    ).fetchone()
    expected = (task_id, task[1], task[12], task[14])
    if claim is None or tuple(claim) != expected:
        _fail(
            "dispute_claim_linkage_invalid",
            "Task dispute does not match its Court claim",
            task_id,
        )


def _classify_legacy_effects(
    connection: sqlite3.Connection,
    task: tuple[object, ...],
    *,
    task_id: str,
    accepted_bid_amount: int | None,
) -> tuple[_LegacyObservation, ...]:
    court_rows = connection.execute(
        "SELECT claim_id, claimant_id, respondent_id, reason, status, filed_at, "
        "rebuttal_deadline FROM court_claims WHERE task_id=? ORDER BY claim_id",
        (task_id,),
    ).fetchall()
    settlement_rows = connection.execute(
        "SELECT r.operation_id, r.operation_kind, r.request_sha256, r.source_reference, "
        "r.event_id, e.status, e.resolved_at FROM bank_operation_receipts r "
        "JOIN bank_escrow e ON e.escrow_id=r.escrow_id WHERE r.escrow_id=? "
        "AND r.operation_kind IN ('escrow_release', 'escrow_split') ORDER BY r.operation_id",
        (task[11],),
    ).fetchall()
    observations: list[_LegacyObservation] = []
    if task[15] is None and court_rows:
        if (
            len(court_rows) != 1
            or settlement_rows
            or not _court_observation_eligible(task, court_rows[0])
        ):
            _fail(
                "legacy_effect_history_unsupported",
                "Unlinked Court history is ambiguous or contradictory",
                task_id,
            )
        claim = tuple(court_rows[0])
        fact = _shared_fact(task, task_id, accepted_bid_amount, str(claim[0]))
        fact.update(
            {
                "claimant_id": claim[1],
                "respondent_id": claim[2],
                "reason": claim[3],
                "claim_status": claim[4],
                "filed_at": claim[5],
                "rebuttal_deadline": claim[6],
            }
        )
        observations.append(
            _observation(
                task_id,
                "court_claim_succeeded",
                fact,
                external_reference=str(claim[0]),
                effect_state="succeeded",
                target_outcome_hint="disputed",
            )
        )
    elif task[15] is not None and len(court_rows) != 1:
        _fail(
            "dispute_claim_linkage_invalid",
            "Task dispute has ambiguous Court history",
            task_id,
        )

    if settlement_rows and task[3] == "submitted":
        if (
            len(settlement_rows) != 1
            or observations
            or not _settlement_observation_eligible(connection, task, tuple(settlement_rows[0]))
        ):
            _fail(
                "legacy_effect_history_unsupported",
                "Worker settlement history is ambiguous or contradictory",
                task_id,
            )
        receipt = tuple(settlement_rows[0])
        leg = connection.execute(
            "SELECT account_id, amount, transaction_id FROM bank_operation_legs "
            "WHERE operation_id=? AND role='recipient'",
            (receipt[0],),
        ).fetchone()
        if leg is None:
            _fail(
                "legacy_effect_history_unsupported",
                "Worker settlement receipt has no recipient leg",
                task_id,
            )
        fact = _shared_fact(task, task_id, accepted_bid_amount, str(receipt[0]))
        fact.update(
            {
                "escrow_status": receipt[5],
                "resolved_at": receipt[6],
                "recipient_id": leg[0],
                "settled_amount": leg[1],
                "settlement_source_reference": receipt[3],
                "external_receipt_request_sha256": receipt[2],
                "external_receipt_event_id": receipt[4],
                "external_receipt_transaction_id": leg[2],
            }
        )
        observations.append(
            _observation(
                task_id,
                "worker_settlement_succeeded",
                fact,
                external_reference=str(receipt[0]),
                effect_state="succeeded",
                target_outcome_hint="approved",
            )
        )
    elif settlement_rows and task[3] not in {"approved", "cancelled", "expired", "ruled"}:
        _fail(
            "legacy_effect_history_unsupported",
            "Settlement outcome does not match a supported task history",
            task_id,
        )

    if task[3] in {"approved", "cancelled", "expired", "ruled"} and task[10] == 0:
        _validate_terminal_financial_history(
            connection,
            task,
            task_id=task_id,
            settlement_rows=settlement_rows,
        )

    if task[10] == 1:
        escrow = connection.execute(
            "SELECT status FROM bank_escrow WHERE escrow_id=? AND task_id=?",
            (task[11], task_id),
        ).fetchone()
        if task[3] != "approved" or settlement_rows or observations or escrow != ("locked",):
            _fail(
                "legacy_effect_history_unsupported",
                "Pending settlement history is ambiguous or contradictory",
                task_id,
            )
        external_reference = _legacy_settlement_reference(task_id)
        fact = _shared_fact(task, task_id, accepted_bid_amount, external_reference)
        fact.update({"escrow_status": "locked", "escrow_pending": 1})
        observations.append(
            _observation(
                task_id,
                "worker_settlement_pending",
                fact,
                external_reference=external_reference,
                effect_state="pending",
                target_outcome_hint="approved",
            )
        )
    elif task[10] != 0:
        _fail(
            "legacy_effect_history_unsupported",
            "Task escrow_pending value is invalid",
            task_id,
        )
    return tuple(observations)


def _validate_terminal_financial_history(
    connection: sqlite3.Connection,
    task: tuple[object, ...],
    *,
    task_id: str,
    settlement_rows: list[sqlite3.Row] | list[tuple[object, ...]],
) -> None:
    if task[3] != "approved":
        _fail(
            "legacy_effect_history_unsupported",
            "Terminal refund or ruling history has no reviewed migration classifier",
            task_id,
        )
    escrow = connection.execute(
        "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id=? AND task_id=?",
        (task[11], task_id),
    ).fetchone()
    if escrow is None or escrow[0] != "released" or escrow[1] is None:
        _fail(
            "legacy_effect_history_unsupported",
            "Approved task does not have a completed worker settlement",
            task_id,
        )
    transactions = connection.execute(
        "SELECT account_id, amount FROM bank_transactions "
        "WHERE type='escrow_release' AND reference=? ORDER BY tx_id",
        (task[11],),
    ).fetchall()
    if len(transactions) != 1 or tuple(transactions[0]) != (task[12], task[2]):
        _fail(
            "legacy_effect_history_unsupported",
            "Approved task settlement does not pay the frozen worker reward",
            task_id,
        )
    if settlement_rows:
        if len(settlement_rows) != 1:
            _fail(
                "legacy_effect_history_unsupported",
                "Approved task has ambiguous native settlement receipts",
                task_id,
            )
        receipt = tuple(settlement_rows[0])
        leg = connection.execute(
            "SELECT account_id, amount, transaction_id FROM bank_operation_legs "
            "WHERE operation_id=? AND role='recipient'",
            (receipt[0],),
        ).fetchone()
        expected_source = f"task:{task_id}:approved"
        if (
            receipt[1] != "escrow_release"
            or receipt[3] != expected_source
            or leg is None
            or tuple(leg)[0:2] != (task[12], task[2])
            or leg[2] is None
        ):
            _fail(
                "legacy_effect_history_unsupported",
                "Approved task native receipt contradicts its frozen settlement terms",
                task_id,
            )


def _court_observation_eligible(
    task: tuple[object, ...], claim: sqlite3.Row | tuple[object, ...]
) -> bool:
    row = tuple(claim)
    return (
        task[3] == "submitted"
        and task[12] is not None
        and task[14] is None
        and row[1] == task[1]
        and row[2] == task[12]
        and isinstance(row[3], str)
        and bool(row[3])
        and isinstance(row[4], str)
        and bool(row[4])
        and isinstance(row[5], str)
        and isinstance(row[6], str)
    )


def _settlement_observation_eligible(
    connection: sqlite3.Connection,
    task: tuple[object, ...],
    receipt: tuple[object, ...],
) -> bool:
    leg = connection.execute(
        "SELECT account_id, amount, transaction_id FROM bank_operation_legs "
        "WHERE operation_id=? AND role='recipient'",
        (receipt[0],),
    ).fetchone()
    return leg is not None and (
        receipt[1] == "escrow_release"
        and receipt[5] == "released"
        and receipt[6] is not None
        and leg[0] == task[12]
        and leg[1] == task[2]
        and leg[2] is not None
    )


def _shared_fact(
    task: tuple[object, ...],
    task_id: str,
    accepted_bid_amount: int | None,
    external_reference: str,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_status": task[3],
        "poster_id": task[1],
        "worker_id": task[12],
        "escrow_id": task[11],
        "reward": task[2],
        "accepted_bid_id": task[13],
        "accepted_bid_amount": accepted_bid_amount,
        "external_reference": external_reference,
    }


def _legacy_settlement_reference(task_id: str) -> str:
    from service_auth.financial_identity import operation_id_for

    return operation_id_for("task-settlement", (task_id, "approved"))


def _observation(
    task_id: str,
    kind: str,
    fact: dict[str, object],
    *,
    external_reference: str,
    effect_state: str,
    target_outcome_hint: str,
) -> _LegacyObservation:
    canonical = _canonical_json(fact)
    identity = uuid5(NAMESPACE_URL, f"agent-economy:mp07:{task_id}:{kind}:{external_reference}")
    return _LegacyObservation(
        observation_id=f"obs-{identity}",
        task_id=task_id,
        observation_kind=kind,
        observed_fact_json=canonical,
        observed_fact_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        external_reference=external_reference,
        effect_state=effect_state,
        target_outcome_hint=target_outcome_hint,
        unresolved=1,
    )


def _migrate_bound(
    bound: BackupBoundSource,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    busy_timeout_ms: int,
) -> MigrationResult:
    try:
        writer = _open_live_writer(bound.source_database_path, busy_timeout_ms)
    except BaseException as primary:
        observation = _observe(bound.source_database_path, admission.snapshot)
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "source_open_failed",
                "observation": observation,
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "error_type": type(primary).__name__,
            },
            primary,
        )
        details: dict[str, object] = {"observation": observation}
        if evidence is not None:
            details["evidence_path"] = str(evidence)
        raise MigrationError(
            "source_open_failed", "Live source could not be opened read-write", details
        ) from primary
    transaction_started = False
    commit_started = False
    try:
        _configure_migration_connection(writer, busy_timeout_ms)
        bound.verify_during_write()
        writer.execute("BEGIN IMMEDIATE")
        transaction_started = True
        _validate_live_v2(writer, admission)
        bound.verify_during_write()
        _apply_v3(
            writer,
            admission=admission,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        _fault("before_validation")
        _validate_v3_transaction(
            writer,
            admission=admission,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
        )
        bound.verify_during_write()
        _fault("before_commit")
        commit_started = True
        _commit(writer)
        transaction_started = False
    except BaseException as primary:
        cleanup_codes: list[str] = []
        if transaction_started and not commit_started:
            cleanup_codes.extend(_rollback_with_note(writer, primary))
        cleanup, _close_error = _dispose_writer(writer, primary)
        cleanup_codes.extend(cleanup)
        observation = _observe(bound.source_database_path, admission.snapshot)
        if commit_started:
            _raise_failure(
                "commit_outcome_uncertain",
                "Migration commit outcome is uncertain",
                primary,
                bound=bound,
                plan=plan,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
                observation=observation,
                cleanup_codes=cleanup_codes,
            )
        _raise_failure(
            "migration_failed",
            "Migration failed before commit",
            primary,
            bound=bound,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            observation=observation,
            cleanup_codes=cleanup_codes,
        )
    return _finalize_committed(
        writer,
        bound=bound,
        admission=admission,
        plan=plan,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
    )


def _apply_v3(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    for statement in _split_sql(plan.sidecar_sql):
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO board_task_contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(
            (
                item.task_id,
                item.lifecycle_version,
                item.timing_contract_version,
                item.acceptance_window_seconds,
                item.bidding_cutoff_us,
                item.acceptance_cutoff_us,
                item.execution_cutoff_us,
                item.review_cutoff_us,
                item.accepted_reward,
                item.accepted_bid_amount,
            )
            for item in admission.contracts
        ),
    )
    connection.executemany(
        "INSERT INTO board_legacy_effect_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(
            (
                item.observation_id,
                item.task_id,
                item.observation_kind,
                item.observed_fact_json,
                item.observed_fact_sha256,
                item.external_reference,
                item.effect_state,
                item.target_outcome_hint,
                item.unresolved,
            )
            for item in admission.observations
        ),
    )
    connection.execute("DROP TABLE schema_metadata")
    connection.execute(plan.metadata_sql)
    connection.execute(
        "INSERT INTO schema_metadata VALUES (1, 3, 2, ?, ?)",
        (backup_id, manifest_sha256),
    )


def _validate_live_v2(connection: sqlite3.Connection, admission: _Admission) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V2:
        raise MigrationError("live_source_changed", "Live source is no longer exact v2", {})
    if _database_snapshot(connection, tables=None) != admission.snapshot:
        raise MigrationError("live_source_changed", "Live source data changed after backup", {})
    metadata = connection.execute("SELECT * FROM schema_metadata").fetchone()
    if metadata is None or tuple(metadata) != admission.metadata:
        raise MigrationError("live_source_changed", "Live source metadata changed after backup", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Live source money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Live source receipt audit failed", {})
    contracts, observations = _derive_task_contracts(connection)
    if contracts != admission.contracts or observations != admission.observations:
        raise MigrationError("live_source_changed", "Live task history changed after backup", {})


def _validate_v3_transaction(
    connection: sqlite3.Connection,
    *,
    admission: _Admission,
    backup_id: str,
    manifest_sha256: str,
) -> None:
    if classify_schema(connection).state is not SchemaState.CURRENT_V3:
        raise MigrationError("v3_validation_failed", "Migrated schema is not exact v3", {})
    _validate_database_health(connection)
    if not audit_money(connection).financially_clean:
        raise MigrationError("money_audit_failed", "Migrated money audit failed", {})
    if not audit_receipt_ownership(connection).ownership_clean:
        raise MigrationError("receipt_audit_failed", "Migrated receipt audit failed", {})
    task_audit = audit_task_contracts(connection)
    if not task_audit.contracts_clean:
        raise MigrationError(
            "task_contract_audit_failed",
            "Migrated task-contract audit failed",
            {"finding_codes": sorted({finding.code for finding in task_audit.findings})},
        )
    preserved = _database_snapshot(connection, tables=tuple(admission.snapshot))
    if preserved != admission.snapshot:
        raise MigrationError("data_preservation_failed", "Existing domain data changed", {})
    if connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() != (0,):
        raise MigrationError("native_history_invented", "Migration created native operations", {})
    if connection.execute("SELECT * FROM schema_metadata").fetchone() != (
        1,
        3,
        2,
        backup_id,
        manifest_sha256,
    ):
        raise MigrationError("provenance_failed", "Migrated metadata is incorrect", {})


def _finalize_committed(
    writer: sqlite3.Connection,
    *,
    bound: BackupBoundSource,
    admission: _Admission,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
) -> MigrationResult:
    cleanup_codes, cleanup_error = _dispose_writer(writer, None)
    try:
        bound.verify_during_write()
        connection = _open_read_only(bound.source_database_path)
        primary: BaseException | None = None
        try:
            _validate_v3_transaction(
                connection,
                admission=admission,
                backup_id=backup_id,
                manifest_sha256=manifest_sha256,
            )
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _close_read_only(connection, primary=primary, label="fresh v3 validation")
    except BaseException as primary:
        if cleanup_error is not None:
            primary.add_note(f"Dynamic writer close also failed: {cleanup_error!r}")
        _raise_failure(
            "post_commit_validation_failed",
            "Migration committed but post-commit validation failed",
            primary,
            bound=bound,
            plan=plan,
            backup_id=backup_id,
            manifest_sha256=manifest_sha256,
            observation=_observe(bound.source_database_path, admission.snapshot),
            cleanup_codes=cleanup_codes,
        )
    if cleanup_codes != ["native_close_completed"]:
        cleanup_primary = MigrationError(
            "post_commit_cleanup_failed",
            "Migration committed but writer cleanup failed",
            {
                "observation": _observe(bound.source_database_path, admission.snapshot),
                "cleanup_codes": cleanup_codes,
                "native_close_completed": True,
            },
        )
        evidence = _write_evidence_with_note(
            bound,
            {
                "status": "post_commit_cleanup_failed",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
                "cleanup_codes": cleanup_codes,
                "native_close_completed": True,
            },
            cleanup_primary,
        )
        if evidence is not None:
            cleanup_primary.safe_details["evidence_path"] = str(evidence)
        raise cleanup_primary from cleanup_error
    try:
        evidence = _write_evidence(
            bound,
            {
                "status": "committed_v3_verified",
                "backup_id": backup_id,
                "manifest_sha256": manifest_sha256,
                "plan_sha256": plan.sha256,
            },
        )
    except BaseException as primary:
        raise MigrationError(
            "post_commit_evidence_failed",
            "Migration committed but evidence publication failed",
            {"observation": "committed_v3_observed"},
        ) from primary
    return MigrationResult(
        applied=True,
        schema_version=3,
        backup_id=backup_id,
        manifest_sha256=manifest_sha256,
        plan_sha256=plan.sha256,
        observation="committed_v3_verified",
        evidence_path=evidence,
    )


def _database_snapshot(
    connection: sqlite3.Connection,
    *,
    tables: tuple[str, ...] | None,
) -> dict[str, object]:
    selected = (
        list(tables)
        if tables is not None
        else [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'schema_metadata' ORDER BY name"
            ).fetchall()
        ]
    )
    if (
        tables is None
        and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
        ).fetchone()
    ):
        selected.append("sqlite_sequence")
    snapshot: dict[str, object] = {}
    for table in selected:
        columns = [
            (int(row[0]), str(row[1]), int(row[5]))
            for row in connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()  # nosec B608
            if int(row[6]) == 0
        ]
        projection = ", ".join(f'"{name}"' for _ordinal, name, _pk in columns)
        table_info = connection.execute("PRAGMA table_list").fetchall()
        without_rowid = next(int(row[4]) for row in table_info if str(row[1]) == table)
        if without_rowid:
            keys = [name for _ordinal, name, pk in sorted(columns, key=lambda item: item[2]) if pk]
            order = ", ".join(f'"{name}"' for name in keys)
            query = f'SELECT {projection} FROM "{table}" ORDER BY {order}'  # nosec B608
        else:
            query = f'SELECT rowid, {projection} FROM "{table}" ORDER BY rowid'  # nosec B608
        rows = connection.execute(query).fetchall()
        snapshot[table] = [[_snapshot_value(value) for value in tuple(row)] for row in rows]
    return snapshot


def _plan() -> _Plan:
    from pathlib import Path

    root = Path(__file__).with_name("schema_resources")
    sidecar = (root / "task_operations_v3.sql").read_text()
    temporary = sqlite3.connect(":memory:")
    try:
        temporary.executescript(load_schema_sql(SchemaState.CURRENT_V3))
        row = temporary.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='schema_metadata'"
        ).fetchone()
    finally:
        temporary.close()
    if row is None or not isinstance(row[0], str):
        raise MigrationError("plan_invalid", "Current v3 metadata SQL is unavailable", {})
    plan_hash = hashlib.sha256((sidecar + "\n" + row[0]).encode()).hexdigest()
    return _Plan(sidecar, row[0], plan_hash)


def _observe(source_path: Path, original_snapshot: dict[str, object]) -> str:
    try:
        connection = _open_read_only(source_path)
    except sqlite3.Error:
        return "unreadable_state"
    try:
        state = classify_schema(connection).state
        if state is SchemaState.CURRENT_V3:
            return "committed_v3_observed"
        snapshot = _database_snapshot(connection, tables=None)
        if state is SchemaState.CURRENT_V2 and snapshot == original_snapshot:
            return "not_applied_v2_observed"
        return "inconsistent_state_observed"
    finally:
        connection.close()


def _raise_failure(
    reason: str,
    message: str,
    primary: BaseException,
    *,
    bound: BackupBoundSource,
    plan: _Plan,
    backup_id: str,
    manifest_sha256: str,
    observation: str,
    cleanup_codes: list[str],
) -> Never:
    evidence = _write_evidence_with_note(
        bound,
        {
            "status": reason,
            "observation": observation,
            "backup_id": backup_id,
            "manifest_sha256": manifest_sha256,
            "plan_sha256": plan.sha256,
            "cleanup_codes": cleanup_codes,
            "error_type": type(primary).__name__,
        },
        primary,
    )
    details: dict[str, object] = {
        "observation": observation,
        "cleanup_codes": cleanup_codes,
        "native_close_completed": "native_close_completed" in cleanup_codes,
    }
    if evidence is not None:
        details["evidence_path"] = str(evidence)
    if isinstance(primary, MigrationError) and reason != "commit_outcome_uncertain":
        primary.safe_details.update(details)
        raise primary
    raise MigrationError(reason, message, details) from primary


def _commit(connection: sqlite3.Connection) -> None:
    connection.execute("COMMIT").close()
    if connection.in_transaction:
        raise sqlite3.OperationalError("explicit COMMIT did not end the transaction")


def _fault(_point: str) -> None:
    return


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _fail(reason: str, message: str, task_id: str) -> Never:
    raise MigrationError(reason, message, {"task_id": task_id})


__all__ = ["migrate_v2_to_v3"]
