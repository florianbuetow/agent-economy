"""Native V6 artifact binding for atomic bid acceptance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from service_commons.exceptions import ServiceError

from db_gateway_service.services.legacy_artifact_continuation import (
    LegacyAcceptanceRecord,
    build_live_legacy_acceptance_observation,
    validate_legacy_publication_provenance,
)
from db_gateway_service.services.prepublication_integrity import assert_publication_integrity
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3

_TABLES: Final[frozenset[str]] = frozenset(
    {
        "board_task_artifact_contracts",
        "board_artifact_manifests",
        "board_artifact_uploads",
        "board_artifact_upload_attempts",
        "board_legacy_artifact_observations",
        "board_artifact_archive_attempts",
        "board_artifact_cutover",
    }
)


@dataclass(frozen=True, slots=True)
class NativeAcceptanceBinding:
    task_id: str
    kind: Literal["publication_contract"]
    sha256: str


@dataclass(frozen=True, slots=True)
class LegacyAcceptancePlan:
    task_id: str
    kind: Literal["legacy_accepted_continuation"]
    record: LegacyAcceptanceRecord

    @property
    def sha256(self) -> str:
        return self.record.observation.observation_sha256


@dataclass(frozen=True, slots=True)
class EmptyInputAcceptanceBinding:
    """Protocol-1 task posted without inputs: bind an explicit empty input manifest."""

    task_id: str
    kind: Literal["publication_contract"]
    sha256: str
    publication_operation_id: str
    poster_id: str
    base_request_sha256: str
    manifest_id: str
    canonical_manifest_json: str
    manifest_sha256: str
    frozen_at_us: int


type AcceptedArtifactBinding = (
    NativeAcceptanceBinding | LegacyAcceptancePlan | EmptyInputAcceptanceBinding
)


def _error(code: str, message: str, status: int) -> ServiceError:
    return ServiceError(code, message, status, {})


def _publication(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
    row = cursor.execute(
        "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
        "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
        "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
        "creation_event_id,result_json,error_json,request_protocol_version,expected_input_count,"
        "base_request_sha256,freeze_operation_id,canonical_freeze_request_json,"
        "freeze_request_sha256,input_manifest_sha256,publication_contract_sha256,"
        "inputs_frozen_at_us,publish_operation_id,canonical_publish_request_json,"
        "publish_request_sha256,publish_authorized_at_us FROM board_task_publications "
        "WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    return None if row is None else tuple(row)


def _legacy_invalid() -> ServiceError:
    return _error("legacy_observation_invalid", "Legacy acceptance evidence is invalid", 500)


def _legacy_task_facts(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    bid_id: str,
    bid_amount: int,
    accepted_at_us: int,
    execution_cutoff_us: int,
    target_lifecycle_version: int,
    source_publication_request_sha256: object,
) -> LegacyAcceptanceRecord:
    task = cursor.execute(
        "SELECT task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
        "deadline_seconds,review_deadline_seconds,bidding_deadline,escrow_id,created_at "
        "FROM board_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    timing = cursor.execute(
        "SELECT lifecycle_version,timing_contract_version,bidding_cutoff_us,"
        "execution_cutoff_us,accepted_reward,accepted_bid_amount "
        "FROM board_task_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    bid = cursor.execute(
        "SELECT bid_id,task_id,bidder_id,proposal,amount,submitted_at FROM board_bids "
        "WHERE bid_id=? AND task_id=?",
        (bid_id, task_id),
    ).fetchone()
    escrow = cursor.execute(
        "SELECT escrow_id,payer_account_id,amount,task_id,status FROM bank_escrow "
        "WHERE escrow_id=(SELECT escrow_id FROM board_tasks WHERE task_id=?)",
        (task_id,),
    ).fetchone()
    if escrow is None:
        raise _error(
            "legacy_continuation_unavailable",
            "Complete legacy continuation evidence is unavailable",
            409,
        )
    if task is None or timing is None or bid is None:
        raise _legacy_invalid()
    try:
        created_at_us = parse_utc_text_to_epoch_us(task[11])
        submitted_at_us = parse_utc_text_to_epoch_us(bid[5])
        bidding_deadline_us = parse_utc_text_to_epoch_us(task[9])
        if (
            task[0] != task_id
            or task[1] != poster_id
            or task[5] != "open"
            or timing[0] != target_lifecycle_version - 1
            or timing[2] != bidding_deadline_us
            or any(timing[index] is not None for index in (3, 4, 5))
            or bid[0] != bid_id
            or bid[1] != task_id
            or bid[2] != worker_id
            or bid[4] != bid_amount
            or escrow[0] != task[10]
            or escrow[1] != poster_id
            or escrow[2] != task[4]
            or escrow[3] != task_id
            or escrow[4] != "locked"
            or created_at_us > submitted_at_us
        ):
            raise _legacy_invalid()
        values: dict[str, object] = {
            "format_version": 1,
            "artifact_contract_version": 0,
            "binding_kind": "legacy_accepted_continuation",
            "observation_status": "verified",
            "task_id": task[0],
            "poster_id": task[1],
            "title": task[2],
            "spec": task[3],
            "reward": task[4],
            "bidding_deadline_seconds": task[6],
            "deadline_seconds": task[7],
            "review_deadline_seconds": task[8],
            "bidding_deadline": task[9],
            "escrow_id": escrow[0],
            "escrow_payer_id": escrow[1],
            "escrow_amount": escrow[2],
            "escrow_status": escrow[4],
            "accepted_bid_id": bid[0],
            "worker_id": bid[2],
            "winning_bid_amount": bid[4],
            "winning_bid_proposal": bid[3],
            "winning_bid_submitted_at": bid[5],
            "accepted_at": epoch_us_to_utc_text(accepted_at_us),
            "execution_cutoff_at": epoch_us_to_utc_text(execution_cutoff_us),
            "execution_cutoff_us": execution_cutoff_us,
            "timing_contract_version": timing[1],
            "accepted_reward": task[4],
            "accepted_bid_amount": bid[4],
            "lifecycle_status": "accepted",
            "lifecycle_version": target_lifecycle_version,
            "input_provenance": "unknown_pre_v6",
            "source_publication_request_sha256": source_publication_request_sha256,
        }
        return build_live_legacy_acceptance_observation(
            values,
            observed_at_us=accepted_at_us,
        )
    except ServiceError:
        raise
    except (TypeError, ValueError) as exc:
        raise _legacy_invalid() from exc


def _legacy_acceptance_plan(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    bid_id: str,
    bid_amount: int,
    accepted_at_us: int,
    execution_cutoff_us: int,
    target_lifecycle_version: int,
    contract: tuple[Any, ...],
) -> LegacyAcceptancePlan:
    if (
        contract[0] != 0
        or contract[1] != "unknown_pre_v6"
        or any(contract[index] is not None for index in (2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13))
    ):
        raise _legacy_invalid()
    observation = cursor.execute(
        "SELECT 1 FROM board_legacy_artifact_observations WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if observation is not None:
        raise _error(
            "legacy_observation_conflict",
            "Legacy acceptance evidence already exists",
            500,
        )
    source_sha256 = contract[10]
    validate_legacy_publication_provenance(
        cursor,
        task_id=task_id,
        poster_id=poster_id,
        source_publication_request_sha256=source_sha256,
    )
    record = _legacy_task_facts(
        cursor,
        task_id=task_id,
        poster_id=poster_id,
        worker_id=worker_id,
        bid_id=bid_id,
        bid_amount=bid_amount,
        accepted_at_us=accepted_at_us,
        execution_cutoff_us=execution_cutoff_us,
        target_lifecycle_version=target_lifecycle_version,
        source_publication_request_sha256=source_sha256,
    )
    return LegacyAcceptancePlan(task_id, "legacy_accepted_continuation", record)


def _is_ungoverned_protocol_v1_task(cursor: sqlite3.Cursor, task_id: str) -> bool:
    """A task without a contract is ungoverned only on an uncut-over DB without V2 publication."""
    if cursor.execute("SELECT 1 FROM board_artifact_cutover LIMIT 1").fetchone() is not None:
        return False
    return (
        cursor.execute(
            "SELECT 1 FROM board_task_publications WHERE task_id=? AND request_protocol_version<>1",
            (task_id,),
        ).fetchone()
        is None
    )


def publication_contract_sha256(base_request_sha256: str, input_manifest_sha256: str) -> str:
    """Derive the V1 artifact contract hash from the base request and input manifest."""
    canonical_contract = json.dumps(
        {
            "artifact_contract_version": 1,
            "base_request_sha256": base_request_sha256,
            "input_manifest_sha256": input_manifest_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical_contract.encode()).hexdigest()


def empty_input_manifest(*, manifest_id: str, task_id: str, poster_id: str) -> tuple[str, str]:
    """Canonical bytes and SHA-256 of an input manifest with zero artifacts."""
    rendered = json.dumps(
        {
            "format_version": 1,
            "manifest_id": manifest_id,
            "task_id": task_id,
            "kind": "input",
            "actor_id": poster_id,
            "artifacts": [],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered, hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _empty_input_binding(
    cursor: sqlite3.Cursor, *, task_id: str, poster_id: str, accepted_at_us: int
) -> EmptyInputAcceptanceBinding | None:
    publication = cursor.execute(
        "SELECT operation_id,request_sha256 FROM board_task_publications "
        "WHERE task_id=? AND actor_id=? AND state='published' AND request_protocol_version=1",
        (task_id, poster_id),
    ).fetchone()
    if publication is None:
        return None
    operation_id = str(publication[0])
    base_request_sha256 = str(publication[1])
    manifest_id = f"manifest-input-{operation_id}"
    manifest_json, manifest_sha256 = empty_input_manifest(
        manifest_id=manifest_id, task_id=task_id, poster_id=poster_id
    )
    return EmptyInputAcceptanceBinding(
        task_id=task_id,
        kind="publication_contract",
        sha256=publication_contract_sha256(base_request_sha256, manifest_sha256),
        publication_operation_id=operation_id,
        poster_id=poster_id,
        base_request_sha256=base_request_sha256,
        manifest_id=manifest_id,
        canonical_manifest_json=manifest_json,
        manifest_sha256=manifest_sha256,
        frozen_at_us=accepted_at_us,
    )


def inspect_acceptance_binding(
    cursor: sqlite3.Cursor,
    *,
    task_id: str,
    poster_id: str,
    worker_id: str,
    bid_id: str,
    bid_amount: int,
    accepted_at_us: int,
    execution_cutoff_us: int,
    target_lifecycle_version: int,
) -> AcceptedArtifactBinding | None:
    """Validate complete governed evidence before the first acceptance write."""
    present = {
        str(row[0])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?,?,?)",
            tuple(sorted(_TABLES)),
        ).fetchall()
    }
    if not present:
        return None
    if present != set(_TABLES):
        raise _error("artifact_governance_incomplete", "Artifact governance is incomplete", 500)
    contract = cursor.execute(
        "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
        "base_request_sha256,input_manifest_id,input_manifest_sha256,"
        "publication_contract_sha256,accepted_publication_contract_sha256,"
        "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
        "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
        "input_actor_id FROM board_task_artifact_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if contract is None:
        if _is_ungoverned_protocol_v1_task(cursor, task_id):
            return _empty_input_binding(
                cursor, task_id=task_id, poster_id=poster_id, accepted_at_us=accepted_at_us
            )
        raise _error("artifact_contract_missing", "Task artifact contract is missing", 409)
    if type(contract[0]) is int and contract[0] == 0:
        return _legacy_acceptance_plan(
            cursor,
            task_id=task_id,
            poster_id=poster_id,
            worker_id=worker_id,
            bid_id=bid_id,
            bid_amount=bid_amount,
            accepted_at_us=accepted_at_us,
            execution_cutoff_us=execution_cutoff_us,
            target_lifecycle_version=target_lifecycle_version,
            contract=tuple(contract),
        )
    if type(contract[0]) is not int or contract[0] != 1 or contract[1] != "native_v6":
        raise _error("artifact_contract_invalid", "Native artifact contract is invalid", 500)
    publication_id = contract[11]
    publication = None if publication_id is None else _publication(cursor, str(publication_id))
    if publication is None:
        raise _error("artifact_contract_invalid", "Native artifact contract is invalid", 500)
    try:
        assert_publication_integrity(cursor, publication)
    except ServiceError as exc:
        raise _error(
            "artifact_contract_invalid",
            "Native artifact contract is invalid",
            500,
        ) from exc
    manifest = cursor.execute(
        "SELECT kind,actor_id,state,manifest_sha256,publication_operation_id,public_task_id "
        "FROM board_artifact_manifests WHERE manifest_id=?",
        (contract[4],),
    ).fetchone()
    derived = publication_contract_sha256(str(contract[3]), str(contract[5]))
    if (
        contract[0] != 1
        or contract[2] is not None
        or contract[7] is not None
        or any(contract[index] is not None for index in (8, 9, 10))
        or contract[12] != "input"
        or contract[13] != poster_id
        or contract[6] != derived
        or publication[2] != task_id
        or publication[6] != poster_id
        or publication[8] != "published"
        or publication[19] != contract[3]
        or publication[23] != contract[5]
        or publication[24] != contract[6]
        or manifest is None
        or tuple(manifest) != ("input", poster_id, "frozen", contract[5], publication_id, None)
    ):
        raise _error("artifact_contract_invalid", "Native artifact contract is invalid", 500)
    return NativeAcceptanceBinding(task_id, "publication_contract", str(contract[6]))


def commit_acceptance_binding(
    cursor: sqlite3.Cursor,
    *,
    binding: AcceptedArtifactBinding,
) -> None:
    if isinstance(binding, EmptyInputAcceptanceBinding):
        _commit_empty_input_binding(cursor, binding)
        return
    if isinstance(binding, LegacyAcceptancePlan):
        observation = binding.record.observation
        cursor.execute(
            "INSERT INTO board_legacy_artifact_observations "
            "(task_id,observation_status,canonical_observation_json,observation_sha256,"
            "observed_at_us) VALUES (?,?,?,?,?)",
            (
                binding.task_id,
                observation.observation_status,
                observation.canonical_json,
                observation.observation_sha256,
                binding.record.observed_at_us,
            ),
        )
        if cursor.rowcount != 1:
            raise _error(
                "task_operation_persistence_failed",
                "Task operation could not be persisted atomically",
                500,
            )
        cursor.execute(
            "UPDATE board_task_artifact_contracts SET accepted_binding_kind=?,"
            "legacy_acceptance_observation_sha256=?,legacy_acceptance_observation_status=? "
            "WHERE task_id=? AND artifact_contract_version=0 "
            "AND input_provenance='unknown_pre_v6' AND accepted_binding_kind IS NULL "
            "AND accepted_publication_contract_sha256 IS NULL "
            "AND legacy_acceptance_observation_sha256 IS NULL "
            "AND legacy_acceptance_observation_status IS NULL",
            (
                binding.kind,
                observation.observation_sha256,
                observation.observation_status,
                binding.task_id,
            ),
        )
        if cursor.rowcount != 1:
            raise _error("task_version_conflict", "Task changed during acceptance", 409)
        return
    cursor.execute(
        "UPDATE board_task_artifact_contracts SET accepted_binding_kind=?,"
        "accepted_publication_contract_sha256=? WHERE task_id=? "
        "AND artifact_contract_version=1 AND input_provenance='native_v6' "
        "AND publication_contract_sha256=? AND accepted_binding_kind IS NULL "
        "AND accepted_publication_contract_sha256 IS NULL",
        (binding.kind, binding.sha256, binding.task_id, binding.sha256),
    )
    if cursor.rowcount != 1:
        raise _error("task_version_conflict", "Task changed during acceptance", 409)


def _commit_empty_input_binding(
    cursor: sqlite3.Cursor, binding: EmptyInputAcceptanceBinding
) -> None:
    cursor.execute(
        "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,format_version,"
        "state,canonical_manifest_json,manifest_sha256,frozen_at_us,publication_operation_id,"
        "public_task_id) VALUES (?,?,'input',?,1,'frozen',?,?,?,?,NULL)",
        (
            binding.manifest_id,
            binding.task_id,
            binding.poster_id,
            binding.canonical_manifest_json,
            binding.manifest_sha256,
            binding.frozen_at_us,
            binding.publication_operation_id,
        ),
    )
    cursor.execute(
        "INSERT INTO board_task_artifact_contracts (task_id,artifact_contract_version,"
        "input_provenance,accepted_binding_kind,base_request_sha256,input_manifest_id,"
        "input_manifest_sha256,publication_contract_sha256,accepted_publication_contract_sha256,"
        "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
        "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
        "input_actor_id) VALUES (?,1,'native_v6',?,?,?,?,?,?,NULL,NULL,NULL,?,'input',?)",
        (
            binding.task_id,
            binding.kind,
            binding.base_request_sha256,
            binding.manifest_id,
            binding.manifest_sha256,
            binding.sha256,
            binding.sha256,
            binding.publication_operation_id,
            binding.poster_id,
        ),
    )
    if cursor.rowcount != 1:
        raise _error(
            "task_operation_persistence_failed",
            "Task operation could not be persisted atomically",
            500,
        )


__all__ = [
    "AcceptedArtifactBinding",
    "EmptyInputAcceptanceBinding",
    "LegacyAcceptancePlan",
    "NativeAcceptanceBinding",
    "commit_acceptance_binding",
    "empty_input_manifest",
    "inspect_acceptance_binding",
    "publication_contract_sha256",
]
