"""T-122 legacy-effect classification proofs for the v2-to-v3 migration.

The producer is absent at the current baseline, so one required capability
assertion fails before the prepared histories run. Once present, each history
is backed up after fixture construction and must migrate without fabricating a
native Board command, caller, actor, command hash, or client operation ID.

Every observed fact is flat. Shared keys are task/status/parties/escrow/reward,
accepted bid terms and external_reference. Succeeded settlement facts add exact
escrow resolution, recipient, amount, settlement source and externally prefixed
Bank receipt evidence. Pending settlement facts add escrow status/pending only.
Court facts add exact claim parties, reason, status and filed/rebuttal timestamps.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

import pytest
from service_auth.financial_identity import operation_id_for

from db_gateway_service import migration, recovery
from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, classify_schema
from tests.unit import test_mp06_migration as old_migration
from tests.unit import test_mp07_migration_contract as parent_contract

if TYPE_CHECKING:
    from pathlib import Path

    from service_commons.recovery_contract import RecoveryInventoryV1

pytestmark = pytest.mark.unit

_OBSERVATION_COLUMNS = (
    "observation_id",
    "task_id",
    "observation_kind",
    "observed_fact_json",
    "observed_fact_sha256",
    "external_reference",
    "effect_state",
    "target_outcome_hint",
    "unresolved",
)
_FORBIDDEN_NATIVE_COLUMNS = frozenset(
    {
        "operation_id",
        "client_operation_id",
        "caller_kid",
        "actor_kind",
        "actor_id",
        "command",
        "command_sha256",
        "request_sha256",
    }
)
_SHARED_FACT_KEYS = frozenset(
    {
        "task_id",
        "task_status",
        "poster_id",
        "worker_id",
        "escrow_id",
        "reward",
        "accepted_bid_id",
        "accepted_bid_amount",
        "external_reference",
    }
)
_FACT_KEYS_BY_KIND = {
    "worker_settlement_succeeded": _SHARED_FACT_KEYS
    | {
        "escrow_status",
        "resolved_at",
        "recipient_id",
        "settled_amount",
        "settlement_source_reference",
        "external_receipt_request_sha256",
        "external_receipt_event_id",
        "external_receipt_transaction_id",
    },
    "worker_settlement_pending": _SHARED_FACT_KEYS | {"escrow_status", "escrow_pending"},
    "court_claim_succeeded": _SHARED_FACT_KEYS
    | {
        "claimant_id",
        "respondent_id",
        "reason",
        "claim_status",
        "filed_at",
        "rebuttal_deadline",
    },
}
_CASE_NAMES = (
    "succeeded_worker_settlement",
    "pending_approved_settlement",
    "succeeded_court_claim",
)

type _DatabaseSnapshot = dict[str, object]
type _StorageSnapshot = dict[str, bytes]


class _MigrateV2ToV3(Protocol):
    def __call__(
        self,
        inventory: RecoveryInventoryV1,
        *,
        backup_root: Path,
        backup_id: str,
        work_root: Path,
        busy_timeout_ms: int,
    ) -> migration.MigrationResult: ...


@dataclass(frozen=True)
class _PreservationEvidence:
    database: _DatabaseSnapshot
    metadata: tuple[object, ...]
    storage: _StorageSnapshot
    backup_verification: dict[str, object]
    backup_digest: str


@dataclass(frozen=True)
class _LegacyCase:
    name: str
    observation_kind: str
    effect_state: str
    target_outcome_hint: str
    external_reference: str
    required_fact: dict[str, object]


@dataclass(frozen=True)
class _SettlementEvidence:
    operation_id: str
    request_sha256: str
    event_id: int
    transaction_id: str


def _migration_entry_point() -> _MigrateV2ToV3 | None:
    value = getattr(migration, "migrate_v2_to_v3", None)
    return cast("_MigrateV2ToV3", value) if callable(value) else None


def _metadata(database: Path) -> tuple[object, ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT * FROM schema_metadata").fetchone()
        assert row is not None
        return tuple(row)
    finally:
        connection.close()


def _fresh_verified_backup(
    inventory: RecoveryInventoryV1,
    *,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
) -> dict[str, object]:
    publication = recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    assert publication.state is recovery.PublicationState.COMPLETE
    verification = recovery.verify_backup(
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    ).model_dump(mode="json")
    assert verification["artifact_verified"] is True
    assert verification["snapshot_preserved"] is True
    assert verification["identity_complete"] is True
    return verification


def _preservation_evidence(
    *,
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    backup_id: str,
    database: Path,
    backup_verification: dict[str, object],
) -> _PreservationEvidence:
    return _PreservationEvidence(
        database=parent_contract._snapshot(database),
        metadata=_metadata(database),
        storage=parent_contract._storage_snapshot(inventory.source_root),
        backup_verification=backup_verification,
        backup_digest=old_migration._tree_digest(backup_root / backup_id),
    )


def _assert_preserved(
    before: _PreservationEvidence,
    *,
    inventory: RecoveryInventoryV1,
    backup_root: Path,
    backup_id: str,
    work_root: Path,
    database: Path,
) -> None:
    after = parent_contract._snapshot(database)
    before_rows = before.database["rows"]
    after_rows = after["rows"]
    assert isinstance(before_rows, dict) and isinstance(after_rows, dict)
    assert {name: after_rows[name] for name in before_rows} == before_rows
    assert after["sequence"] == before.database["sequence"]
    before_objects = before.database["objects"]
    after_objects = after["objects"]
    assert isinstance(before_objects, list) and isinstance(after_objects, list)
    assert all(item in after_objects for item in before_objects)
    assert parent_contract._storage_snapshot(inventory.source_root) == before.storage
    assert old_migration._tree_digest(backup_root / backup_id) == before.backup_digest
    assert (
        recovery.verify_backup(
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        ).model_dump(mode="json")
        == before.backup_verification
    )


def _clear_court_history(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM court_rebuttals WHERE claim_id='clm-1'")
    connection.execute("DELETE FROM court_claims WHERE claim_id='clm-1'")
    connection.execute(
        "DELETE FROM events WHERE task_id='t-1' "
        "AND event_type IN ('claim.filed', 'rebuttal.submitted')"
    )
    connection.execute(
        "UPDATE board_tasks SET dispute_reason=NULL, dispute_id=NULL, "
        "rebuttal_deadline=NULL, rebuttal_submitted_at=NULL, disputed_at=NULL "
        "WHERE task_id='t-1'"
    )


def _add_worker_settlement_receipt(connection: sqlite3.Connection) -> _SettlementEvidence:
    """Resolve esc-1 to the worker with one ownership-valid native receipt."""
    operation_id = operation_id_for("task-settlement", ("t-1", "approved"))
    source_reference = "task:t-1:approved"
    request = canonical_request(
        "escrow_release",
        {
            "escrow_id": "esc-1",
            "recipient_account_id": "a-worker",
            "source_reference": source_reference,
        },
    )
    event = connection.execute(
        "INSERT INTO events (event_source, event_type, timestamp, task_id, agent_id, "
        "summary, payload) VALUES ('central-bank', 'escrow.released', "
        "'2026-10-17T12:00:04Z', NULL, 'a-worker', 'Released escrow', '{}')"
    )
    event_id = int(event.lastrowid)
    connection.execute("UPDATE bank_accounts SET balance=50 WHERE account_id='a-worker'")
    connection.execute(
        "INSERT INTO bank_transactions (tx_id, account_id, type, amount, balance_after, "
        "reference, timestamp, event_id) VALUES ('tx-worker-settlement', 'a-worker', "
        "'escrow_release', 50, 50, 'esc-1', '2026-10-17T12:00:04Z', ?)",
        (event_id,),
    )
    connection.execute(
        "UPDATE bank_escrow SET status='released', resolved_at='2026-10-17T12:00:04Z' "
        "WHERE escrow_id='esc-1'"
    )
    connection.execute(
        "INSERT INTO bank_operation_receipts (operation_id, receipt_version, operation_kind, "
        "canonical_request_json, request_sha256, source_reference, committed_at, event_id, "
        "escrow_id, result_json) VALUES (?, 1, 'escrow_release', ?, ?, ?, "
        "'2026-10-17T12:00:04Z', ?, 'esc-1', ?)",
        (
            operation_id,
            request.canonical_json,
            request.request_sha256,
            request.source_reference,
            event_id,
            json.dumps(
                {"escrow_status_at_commit": "released"},
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )
    connection.execute(
        "INSERT INTO bank_operation_legs (operation_id, leg_index, role, account_id, "
        "direction, amount, transaction_id, balance_after) "
        "VALUES (?, 0, 'recipient', 'a-worker', 'credit', 50, 'tx-worker-settlement', 50)",
        (operation_id,),
    )
    return _SettlementEvidence(
        operation_id=operation_id,
        request_sha256=request.request_sha256,
        event_id=event_id,
        transaction_id="tx-worker-settlement",
    )


def _shared_fact(*, task_status: str, external_reference: str) -> dict[str, object]:
    return {
        "task_id": "t-1",
        "task_status": task_status,
        "poster_id": "a-external",
        "worker_id": "a-worker",
        "escrow_id": "esc-1",
        "reward": 50,
        "accepted_bid_id": "bid-1",
        "accepted_bid_amount": 40,
        "external_reference": external_reference,
    }


def _apply_case(connection: sqlite3.Connection, name: str) -> _LegacyCase:
    provisional_reference = operation_id_for("task-settlement", ("t-1", "approved"))
    if name == "succeeded_worker_settlement":
        _clear_court_history(connection)
        connection.execute(
            "UPDATE board_tasks SET status='submitted', approved_at=NULL, "
            "escrow_pending=0 WHERE task_id='t-1'"
        )
        receipt = _add_worker_settlement_receipt(connection)
        return _LegacyCase(
            name=name,
            observation_kind="worker_settlement_succeeded",
            effect_state="succeeded",
            target_outcome_hint="approved",
            external_reference=receipt.operation_id,
            required_fact={
                **_shared_fact(
                    task_status="submitted",
                    external_reference=receipt.operation_id,
                ),
                "escrow_status": "released",
                "resolved_at": "2026-10-17T12:00:04Z",
                "recipient_id": "a-worker",
                "settled_amount": 50,
                "settlement_source_reference": "task:t-1:approved",
                "external_receipt_request_sha256": receipt.request_sha256,
                "external_receipt_event_id": receipt.event_id,
                "external_receipt_transaction_id": receipt.transaction_id,
            },
        )
    if name == "pending_approved_settlement":
        _clear_court_history(connection)
        connection.execute(
            "UPDATE board_tasks SET status='approved', approved_at='2026-10-17T12:00:04Z', "
            "escrow_pending=1 WHERE task_id='t-1'"
        )
        return _LegacyCase(
            name=name,
            observation_kind="worker_settlement_pending",
            effect_state="pending",
            target_outcome_hint="approved",
            external_reference=provisional_reference,
            required_fact={
                **_shared_fact(
                    task_status="approved",
                    external_reference=provisional_reference,
                ),
                "escrow_status": "locked",
                "escrow_pending": 1,
            },
        )
    if name == "succeeded_court_claim":
        connection.execute("DELETE FROM court_rebuttals WHERE claim_id='clm-1'")
        connection.execute(
            "DELETE FROM events WHERE task_id='t-1' AND event_type='rebuttal.submitted'"
        )
        connection.execute(
            "UPDATE court_claims SET status='rebuttal_pending', rebuttal_deadline="
            "'2026-10-17T12:00:11.500000Z' WHERE claim_id='clm-1'"
        )
        connection.execute(
            "UPDATE board_tasks SET status='submitted', dispute_reason=NULL, dispute_id=NULL, "
            "rebuttal_deadline=NULL, rebuttal_submitted_at=NULL, disputed_at=NULL, "
            "escrow_pending=0 WHERE task_id='t-1'"
        )
        return _LegacyCase(
            name=name,
            observation_kind="court_claim_succeeded",
            effect_state="succeeded",
            target_outcome_hint="disputed",
            external_reference="clm-1",
            required_fact={
                **_shared_fact(task_status="submitted", external_reference="clm-1"),
                "claimant_id": "a-external",
                "respondent_id": "a-worker",
                "reason": "reason",
                "claim_status": "rebuttal_pending",
                "filed_at": "2026-10-17T12:00:01.500000Z",
                "rebuttal_deadline": "2026-10-17T12:00:11.500000Z",
            },
        )
    raise AssertionError(f"Unknown legacy fixture: {name}")


def _assert_valid_v2_history(database: Path) -> None:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state is SchemaState.CURRENT_V2
        assert audit_money(connection).financially_clean
        assert audit_receipt_ownership(connection).ownership_clean
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def _assert_source_case_facts(database: Path, case: _LegacyCase) -> None:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        task = connection.execute(
            "SELECT status, poster_id, worker_id, accepted_bid_id, reward, escrow_id, "
            "escrow_pending, approved_at, dispute_reason, dispute_id, disputed_at, "
            "accepted_at, execution_deadline, submitted_at, review_deadline "
            "FROM board_tasks WHERE task_id='t-1'"
        ).fetchone()
        assert task is not None
        assert task[1:6] == ("a-external", "a-worker", "bid-1", 50, "esc-1")
        assert task[11:] == (
            "2026-10-17T12:00:00.500000Z",
            "2026-10-17T12:00:01.500000Z",
            "2026-10-17T12:00:01Z",
            "2026-10-17T12:00:02Z",
        )
        bid = connection.execute(
            "SELECT bidder_id, amount FROM board_bids WHERE bid_id='bid-1' AND task_id='t-1'"
        ).fetchone()
        assert bid == ("a-worker", 40)
        escrow = connection.execute(
            "SELECT payer_account_id, amount, task_id, status, resolved_at "
            "FROM bank_escrow WHERE escrow_id='esc-1'"
        ).fetchone()
        assert escrow is not None and escrow[:3] == ("a-external", 50, "t-1")
        settlement_receipts = connection.execute(
            "SELECT operation_id, operation_kind, source_reference, result_json, "
            "request_sha256, event_id "
            "FROM bank_operation_receipts WHERE escrow_id='esc-1' "
            "AND operation_kind IN ('escrow_release', 'escrow_split')"
        ).fetchall()
        court_claims = connection.execute(
            "SELECT claim_id, claimant_id, respondent_id, reason, status, filed_at, "
            "rebuttal_deadline "
            "FROM court_claims WHERE task_id='t-1'"
        ).fetchall()
        court_events = connection.execute(
            "SELECT event_type FROM events WHERE task_id='t-1' "
            "AND event_type IN ('claim.filed', 'rebuttal.submitted') ORDER BY event_id"
        ).fetchall()

        if case.name == "succeeded_worker_settlement":
            assert task[0] == "submitted"
            assert task[6:11] == (0, None, None, None, None)
            assert escrow[3:] == ("released", "2026-10-17T12:00:04Z")
            assert len(settlement_receipts) == 1
            receipt = settlement_receipts[0]
            assert receipt[:3] == (
                case.external_reference,
                "escrow_release",
                "task:t-1:approved",
            )
            assert json.loads(str(receipt[3])) == {"escrow_status_at_commit": "released"}
            assert receipt[4] == case.required_fact["external_receipt_request_sha256"]
            assert receipt[5] == case.required_fact["external_receipt_event_id"]
            assert connection.execute(
                "SELECT account_id, type, amount, balance_after, reference "
                "FROM bank_transactions WHERE tx_id='tx-worker-settlement'"
            ).fetchone() == ("a-worker", "escrow_release", 50, 50, "esc-1")
            assert court_claims == []
            assert court_events == []
        elif case.name == "pending_approved_settlement":
            assert task[0] == "approved"
            assert task[6:11] == (1, "2026-10-17T12:00:04Z", None, None, None)
            assert escrow[3:] == ("locked", None)
            assert settlement_receipts == []
            assert connection.execute(
                "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?",
                (case.external_reference,),
            ).fetchone() == (0,)
            assert court_claims == []
            assert court_events == []
        elif case.name == "succeeded_court_claim":
            assert task[0] == "submitted"
            assert task[6:11] == (0, None, None, None, None)
            assert escrow[3:] == ("locked", None)
            assert settlement_receipts == []
            assert court_claims == [
                (
                    "clm-1",
                    "a-external",
                    "a-worker",
                    "reason",
                    "rebuttal_pending",
                    "2026-10-17T12:00:01.500000Z",
                    "2026-10-17T12:00:11.500000Z",
                )
            ]
            assert connection.execute(
                "SELECT COUNT(*) FROM court_rebuttals WHERE claim_id='clm-1'"
            ).fetchone() == (0,)
            assert court_events == [("claim.filed",)]
        else:
            raise AssertionError(f"Unknown legacy fixture: {case.name}")
        _assert_observed_fact_contract(case, dict(case.required_fact))
    finally:
        connection.close()


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _assert_no_native_provenance(value: object) -> None:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        assert _FORBIDDEN_NATIVE_COLUMNS.isdisjoint(keys)
        for nested in value.values():
            _assert_no_native_provenance(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _assert_no_native_provenance(nested)


def _assert_observed_fact_contract(case: _LegacyCase, observed_fact: dict[str, object]) -> None:
    _assert_no_native_provenance(observed_fact)
    assert set(observed_fact) == _FACT_KEYS_BY_KIND[case.observation_kind]
    assert all(not isinstance(value, dict | list | tuple) for value in observed_fact.values())
    assert observed_fact == case.required_fact


def _assert_migrated_observation(database: Path, case: _LegacyCase) -> None:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        assert classify_schema(connection).state.value == "current_v3"
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(board_legacy_effect_observations)"
            ).fetchall()
        }
        assert columns == set(_OBSERVATION_COLUMNS)
        assert _FORBIDDEN_NATIVE_COLUMNS.isdisjoint(columns)
        rows = connection.execute(
            "SELECT observation_id, task_id, observation_kind, observed_fact_json, "
            "observed_fact_sha256, external_reference, effect_state, target_outcome_hint, "
            "unresolved FROM board_legacy_effect_observations "
            "WHERE task_id='t-1' ORDER BY observation_id"
        ).fetchall()
        assert len(rows) == 1
        row = dict(zip(_OBSERVATION_COLUMNS, rows[0], strict=True))
        observation_id = row["observation_id"]
        assert isinstance(observation_id, str) and observation_id.startswith("obs-")
        assert not observation_id.startswith("op-")
        assert row["observation_kind"] == case.observation_kind
        assert row["external_reference"] == case.external_reference
        assert row["effect_state"] == case.effect_state
        assert row["target_outcome_hint"] == case.target_outcome_hint
        assert row["unresolved"] == 1
        observed_fact_json = row["observed_fact_json"]
        assert isinstance(observed_fact_json, str)
        observed_fact = json.loads(observed_fact_json)
        assert isinstance(observed_fact, dict)
        _assert_observed_fact_contract(case, observed_fact)
        assert observed_fact_json == _canonical_json(observed_fact)
        assert (
            row["observed_fact_sha256"]
            == hashlib.sha256(observed_fact_json.encode("utf-8")).hexdigest()
        )
        assert connection.execute("SELECT COUNT(*) FROM board_task_operations").fetchone() == (0,)
        assert connection.execute(
            "SELECT task_id, lifecycle_version, timing_contract_version, "
            "acceptance_window_seconds, bidding_cutoff_us, acceptance_cutoff_us, "
            "execution_cutoff_us, review_cutoff_us, accepted_reward, accepted_bid_amount "
            "FROM board_task_contracts WHERE task_id='t-1'"
        ).fetchone() == (
            "t-1",
            0,
            1,
            None,
            1792238401125000,
            1792238401125000,
            1792238401500000,
            1792238402000000,
            50,
            40,
        )
    finally:
        connection.close()


def _assert_case_migrates(
    migrate: _MigrateV2ToV3,
    *,
    name: str,
    tmp_path: Path,
) -> None:
    tmp_path.mkdir()
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        case = _apply_case(connection, name)
        connection.commit()
    finally:
        connection.close()
    _assert_valid_v2_history(database)
    _assert_source_case_facts(database, case)

    backup_id = f"legacy-{name}"
    verification = _fresh_verified_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    before = _preservation_evidence(
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        database=database,
        backup_verification=verification,
    )

    result = migrate(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        busy_timeout_ms=5000,
    )

    assert result.applied and result.schema_version == 3
    assert result.observation == "committed_v3_verified"
    _assert_preserved(
        before,
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
        database=database,
    )
    _assert_migrated_observation(database, case)


def _pending_fact_validator_case() -> _LegacyCase:
    external_reference = operation_id_for("task-settlement", ("t-validator", "approved"))
    return _LegacyCase(
        name="validator_pending",
        observation_kind="worker_settlement_pending",
        effect_state="pending",
        target_outcome_hint="approved",
        external_reference=external_reference,
        required_fact={
            **_shared_fact(task_status="approved", external_reference=external_reference),
            "escrow_status": "locked",
            "escrow_pending": 1,
        },
    )


def test_observed_fact_validator_accepts_exact_flat_contract() -> None:
    case = _pending_fact_validator_case()
    _assert_observed_fact_contract(case, dict(case.required_fact))


@pytest.mark.parametrize("invalid_kind", ["extra_native_key", "nested_native_key"])
def test_observed_fact_validator_rejects_invented_native_provenance(invalid_kind: str) -> None:
    case = _pending_fact_validator_case()
    invalid = dict(case.required_fact)
    if invalid_kind == "extra_native_key":
        invalid["operation_id"] = "op-invented-board-identity"
    else:
        invalid["external_reference"] = {
            "external_receipt": {"caller_kid": "invented-board-caller"}
        }

    with pytest.raises(AssertionError):
        _assert_observed_fact_contract(case, invalid)


@pytest.mark.parametrize("name", _CASE_NAMES)
def test_legacy_effect_source_history_is_valid_v2_and_backup_verified(
    name: str,
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root, database = parent_contract._prepared_v2(tmp_path)
    connection = sqlite3.connect(database)
    try:
        case = _apply_case(connection, name)
        connection.commit()
    finally:
        connection.close()

    _assert_valid_v2_history(database)
    _assert_source_case_facts(database, case)
    backup_id = f"legacy-control-{name}"
    verification = _fresh_verified_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    evidence = _preservation_evidence(
        inventory=inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        database=database,
        backup_verification=verification,
    )
    assert evidence.backup_verification["artifact_verified"] is True
    assert evidence.backup_verification["snapshot_preserved"] is True
    assert evidence.backup_digest != ""


def test_v2_to_v3_classifies_legacy_effects_without_native_history(tmp_path: Path) -> None:
    migrate = _migration_entry_point()
    assert migrate is not None, (
        "Missing explicit migrate_v2_to_v3; legacy-effect classification remains prepared"
    )

    for name in _CASE_NAMES:
        _assert_case_migrates(migrate, name=name, tmp_path=tmp_path / name)
