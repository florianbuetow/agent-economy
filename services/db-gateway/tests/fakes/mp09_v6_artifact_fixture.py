"""Test-only V5 plus proposed V6 artifact contract fixture."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from service_auth.financial_identity import operation_id_for

from db_gateway_service.financial_requests import canonical_request

BOARD_KID: Final[str] = "gateway-task-board"
BODY_A: Final[bytes] = b"alpha artifact\n"
BODY_B: Final[bytes] = b"beta artifact\n"
HASH_A: Final[str] = "6c85b3bd841507f0068026dfb8f71a522355a0b4cf127441fa2c83682f65bac5"
HASH_B: Final[str] = "c886c6d7f5b9d98f55873e786b85fdd728d71a34b6a9fa12780d82cbbbe82369"
NOW_US: Final[int] = 4_070_995_200_000_000
MAX_UPLOAD_DURATION_SECONDS: Final[int] = 900
EXECUTION_DURATION_SECONDS: Final[int] = 3600
EXECUTION_CUTOFF_US: Final[int] = NOW_US + EXECUTION_DURATION_SECONDS * 1_000_000
EXECUTION_CUTOFF_TEXT: Final[str] = "2099-01-02T01:00:00Z"
PUBLISHED_AT_US: Final[int] = NOW_US - 59_000_000
PUBLISHED_AT_TEXT: Final[str] = "2099-01-01T23:59:01Z"
BIDDING_DURATION_SECONDS: Final[int] = 60
BIDDING_CUTOFF_US: Final[int] = PUBLISHED_AT_US + BIDDING_DURATION_SECONDS * 1_000_000
BIDDING_CUTOFF_TEXT: Final[str] = "2099-01-02T00:00:01Z"
ACCEPTANCE_WINDOW_SECONDS: Final[int] = 60
ACCEPTANCE_CUTOFF_US: Final[int] = BIDDING_CUTOFF_US + ACCEPTANCE_WINDOW_SECONDS * 1_000_000
TASK_ID: Final[str] = "t-70000000-0000-4000-8000-000000000001"
PUBLICATION_OPERATION_ID: Final[str] = "op-70000000-0000-4000-8000-000000000001"
FREEZE_OPERATION_ID: Final[str] = "op-70000000-0000-4000-8000-000000000002"
PUBLISH_OPERATION_ID: Final[str] = "op-70000000-0000-4000-8000-000000000003"
INPUT_MANIFEST_ID: Final[str] = "manifest-input-v6-reservation"


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


_BASE_REQUEST: Final[dict[str, object]] = {
    "acceptance_window_seconds": ACCEPTANCE_WINDOW_SECONDS,
    "action": "task_publication_admit",
    "actor_id": "poster-1",
    "actor_kind": "agent",
    "bidding_deadline_seconds": BIDDING_DURATION_SECONDS,
    "execution_deadline_seconds": EXECUTION_DURATION_SECONDS,
    "expected_input_count": 0,
    "lock_operation_id": operation_id_for("task-lock", (TASK_ID, "poster-1")),
    "protocol_version": 2,
    "publication_operation_id": PUBLICATION_OPERATION_ID,
    "resource": "POST /task-publications",
    "review_deadline_seconds": 60,
    "reward": 100,
    "spec": "Return exact text.",
    "task_id": TASK_ID,
    "title": "Artifact task",
}
_BASE_REQUEST_SHA256: Final[str] = _canonical_sha256(_BASE_REQUEST)
_INPUT_MANIFEST: Final[dict[str, object]] = {
    "actor_id": "poster-1",
    "artifacts": [],
    "format_version": 1,
    "kind": "input",
    "manifest_id": INPUT_MANIFEST_ID,
    "task_id": TASK_ID,
}
_INPUT_MANIFEST_JSON: Final[str] = _canonical_json(_INPUT_MANIFEST)
_INPUT_MANIFEST_SHA256: Final[str] = _canonical_sha256(_INPUT_MANIFEST)
_FREEZE_REQUEST: Final[dict[str, object]] = {
    "action": "task_publication_inputs_freeze",
    "actor_id": "poster-1",
    "freeze_operation_id": FREEZE_OPERATION_ID,
    "input_manifest_id": INPUT_MANIFEST_ID,
    "protocol_version": 2,
    "publication_operation_id": PUBLICATION_OPERATION_ID,
    "resource": f"POST /task-publications/{PUBLICATION_OPERATION_ID}/inputs/freeze",
    "task_id": TASK_ID,
}
_FREEZE_REQUEST_JSON: Final[str] = _canonical_json(_FREEZE_REQUEST)
_FREEZE_REQUEST_SHA256: Final[str] = _canonical_sha256(_FREEZE_REQUEST)
NATIVE_BINDING: Final[str] = _canonical_sha256(
    {
        "artifact_contract_version": 1,
        "base_request_sha256": _BASE_REQUEST_SHA256,
        "input_manifest_sha256": _INPUT_MANIFEST_SHA256,
    }
)
_PUBLISH_REQUEST: Final[dict[str, object]] = {
    "action": "task_publication_publish",
    "actor_id": "poster-1",
    "lock_operation_id": _BASE_REQUEST["lock_operation_id"],
    "protocol_version": 2,
    "publication_contract_sha256": NATIVE_BINDING,
    "publication_operation_id": PUBLICATION_OPERATION_ID,
    "publish_operation_id": PUBLISH_OPERATION_ID,
    "resource": "POST /tasks",
    "task_id": TASK_ID,
}
_PUBLISH_REQUEST_JSON: Final[str] = _canonical_json(_PUBLISH_REQUEST)
_PUBLISH_REQUEST_SHA256: Final[str] = _canonical_sha256(_PUBLISH_REQUEST)
_LEGACY_OBSERVATION: Final[dict[str, object]] = {
    "accepted_at": "2099-01-02T00:00:00Z",
    "accepted_bid_amount": 80,
    "accepted_bid_id": "bid-legacy-v6-reservation",
    "accepted_reward": 100,
    "artifact_contract_version": 0,
    "bidding_deadline": BIDDING_CUTOFF_TEXT,
    "bidding_deadline_seconds": BIDDING_DURATION_SECONDS,
    "binding_kind": "legacy_accepted_continuation",
    "deadline_seconds": EXECUTION_DURATION_SECONDS,
    "escrow_amount": 100,
    "escrow_id": f"escrow-{TASK_ID}",
    "escrow_payer_id": "poster-1",
    "escrow_status": "locked",
    "execution_cutoff_at": EXECUTION_CUTOFF_TEXT,
    "execution_cutoff_us": EXECUTION_CUTOFF_US,
    "format_version": 1,
    "input_provenance": "unknown_pre_v6",
    "lifecycle_status": "accepted",
    "lifecycle_version": 1,
    "observation_status": "verified",
    "poster_id": "poster-1",
    "review_deadline_seconds": 60,
    "reward": 100,
    "source_publication_request_sha256": None,
    "spec": "Return exact text.",
    "task_id": TASK_ID,
    "timing_contract_version": 2,
    "title": "Artifact task",
    "winning_bid_amount": 80,
    "winning_bid_proposal": "Deliver exact text.",
    "winning_bid_submitted_at": "2099-01-01T23:59:30Z",
    "worker_id": "worker-1",
}
_LEGACY_OBSERVATION_JSON: Final[str] = _canonical_json(_LEGACY_OBSERVATION)
LEGACY_BINDING: Final[str] = _canonical_sha256(_LEGACY_OBSERVATION)


@dataclass(slots=True)
class MutableClock:
    """Deterministic integer microsecond clock."""

    now_us: int = NOW_US

    def __call__(self) -> int:
        return self.now_us


@dataclass(frozen=True, slots=True)
class V6Api:
    """Dynamically loaded proposed V6 API, avoiding collection-time imports."""

    requests: Any
    writer_type: Any


def require_v6_api() -> V6Api:
    """Return the V6 API or fail in the calling test with a capability assertion."""
    requests_name = "db_gateway_service.artifact_upload_requests"
    writer_name = "db_gateway_service.services.artifact_writer"
    assert importlib.util.find_spec(requests_name) is not None, (
        "Missing V6 capability: db_gateway_service.artifact_upload_requests"
    )
    assert importlib.util.find_spec(writer_name) is not None, (
        "Missing V6 capability: db_gateway_service.services.artifact_writer"
    )
    requests = importlib.import_module(requests_name)
    writer_module = importlib.import_module(writer_name)
    writer_type = getattr(writer_module, "ArtifactWriter", None)
    assert writer_type is not None, "Missing V6 capability: ArtifactWriter"
    for symbol in (
        "parse_artifact_policy",
        "parse_artifact_upload_request",
    ):
        assert callable(getattr(requests, symbol, None)), f"Missing V6 capability: {symbol}"
    return V6Api(requests=requests, writer_type=writer_type)


def open_v6_database(database_path: Path) -> sqlite3.Connection:
    """Compose the fresh schema-V5-labeled V6 prototype without migration SQL."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    resources = Path(__file__).parents[2] / "src" / "db_gateway_service" / "schema_resources"
    try:
        for resource_name in (
            "current_v5.sql",
            "task_operations_v3.sql",
            "publication_v6.sql",
            "task_effect_recovery_v5.sql",
            "artifacts_v6.sql",
        ):
            resource = resources / resource_name
            assert resource.is_file(), f"Missing V6 capability: schema_resources/{resource_name}"
            connection.executescript(resource.read_text(encoding="utf-8"))
        return connection
    except BaseException:
        connection.close()
        raise


def explicit_policy(api: V6Api, **overrides: object) -> Any:
    """Return a strict, complete tiny policy suitable for deterministic tests."""
    values: dict[str, object] = {
        "contract_version": 1,
        "max_file_bytes": 32,
        "max_files_per_manifest": 2,
        "max_manifest_bytes": 32,
        "reservation_ttl_seconds": 300,
        "reservation_renew_before_expiry_seconds": 60,
        "max_upload_duration_seconds": MAX_UPLOAD_DURATION_SECONDS,
    }
    values.update(overrides)
    return api.requests.parse_artifact_policy(values)


def upload_payload(
    *,
    operation_id: str,
    task_id: str,
    manifest_id: str,
    binding_kind: str = "publication_contract",
    binding_sha256: str = NATIVE_BINDING,
    body: bytes = BODY_A,
    content_sha256: str = HASH_A,
    display_filename: str = "answer.txt",
    lifecycle_version: int = 1,
) -> dict[str, object]:
    """Build one exact immutable worker-upload intent."""
    return {
        "protocol_version": 1,
        "upload_operation_id": operation_id,
        "resource": "POST /board/artifact-uploads/reserve",
        "task_id": task_id,
        "manifest_id": manifest_id,
        "actor_id": "worker-1",
        "actor_role": "worker",
        "display_filename": display_filename,
        "evidence_format": "text",
        "declared_size_bytes": len(body),
        "content_sha256": content_sha256,
        "expected_lifecycle_version": lifecycle_version,
        "accepted_binding_kind": binding_kind,
        "accepted_binding_sha256": binding_sha256,
    }


def seed_accepted_task(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    manifest_id: str,
    binding_kind: str = "publication_contract",
    binding_sha256: str = NATIVE_BINDING,
    lifecycle_version: int = 1,
) -> None:
    """Seed coherent historical publication, lock, task, and acceptance facts."""
    selected_bid_id = (
        "bid-native-v6-reservation"
        if binding_kind == "publication_contract"
        else "bid-legacy-v6-reservation"
    )
    connection.executemany(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, '2099-01-01T23:58:00Z')",
        (
            ("poster-1", "Poster", "ed25519:poster-v6-test-key"),
            ("worker-1", "Worker", "ed25519:worker-v6-test-key"),
        ),
    )
    connection.executemany(
        "INSERT INTO bank_accounts (account_id, balance, created_at) "
        "VALUES (?, ?, '2099-01-01T23:58:01Z')",
        (("poster-1", 0), ("worker-1", 0)),
    )
    lock_request = canonical_request(
        "escrow_lock",
        {"payer_account_id": "poster-1", "task_id": task_id, "amount": 100},
    )
    lock_event = connection.execute(
        "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('central-bank','escrow.locked','2099-01-01T23:59:00Z',?,'poster-1',"
        "'Escrow locked',?)",
        (task_id, _canonical_json({"amount": 100, "escrow_id": f"escrow-{task_id}"})),
    ).lastrowid
    assert lock_event is not None
    connection.execute(
        "INSERT INTO bank_escrow (escrow_id, payer_account_id, amount, task_id, status, "
        "created_at,event_id) VALUES (?, 'poster-1', 100, ?, 'locked', "
        "'2099-01-01T23:59:00Z',?)",
        (f"escrow-{task_id}", task_id, lock_event),
    )
    connection.execute(
        "INSERT INTO bank_transactions (tx_id,account_id,type,amount,balance_after,reference,"
        "timestamp,event_id) VALUES (?,'poster-1','escrow_lock',100,0,?,"
        "'2099-01-01T23:59:00Z',?)",
        (f"tx-lock-{task_id}", task_id, lock_event),
    )
    connection.execute(
        "INSERT INTO bank_operation_receipts (operation_id,receipt_version,operation_kind,"
        "canonical_request_json,request_sha256,source_reference,committed_at,event_id,escrow_id,"
        "result_json) VALUES (?,1,'escrow_lock',?,?,?,'2099-01-01T23:59:00Z',?,?,?)",
        (
            _BASE_REQUEST["lock_operation_id"],
            lock_request.canonical_json,
            lock_request.request_sha256,
            task_id,
            lock_event,
            f"escrow-{task_id}",
            _canonical_json({"escrow_status_at_commit": "locked"}),
        ),
    )
    connection.execute(
        "INSERT INTO bank_operation_legs (operation_id,leg_index,role,account_id,direction,"
        "amount,transaction_id,balance_after) VALUES (?,0,'payer','poster-1','debit',100,?,0)",
        (_BASE_REQUEST["lock_operation_id"], f"tx-lock-{task_id}"),
    )
    creation_event = connection.execute(
        "INSERT INTO events (event_source,event_type,timestamp,task_id,agent_id,summary,payload) "
        "VALUES ('task-board','task.created',?,?,'poster-1',"
        "'Task created',?)",
        (
            PUBLISHED_AT_TEXT,
            task_id,
            _canonical_json({"reward": 100, "title": "Artifact task"}),
        ),
    ).lastrowid
    assert creation_event is not None
    connection.execute(
        "INSERT INTO board_tasks (task_id, poster_id, title, spec, reward, status, "
        "bidding_deadline_seconds, deadline_seconds, review_deadline_seconds, "
        "bidding_deadline, execution_deadline, escrow_id, worker_id, accepted_bid_id, "
        "accepted_at, created_at) "
        "VALUES (?, 'poster-1', 'Artifact task', 'Return exact text.', 100, 'accepted', "
        "60, 3600, 60, ?, ?, ?, 'worker-1', ?, '2099-01-02T00:00:00Z', ?)",
        (
            task_id,
            BIDDING_CUTOFF_TEXT,
            EXECUTION_CUTOFF_TEXT,
            f"escrow-{task_id}",
            selected_bid_id,
            PUBLISHED_AT_TEXT,
        ),
    )
    connection.execute(
        "INSERT INTO board_bids (bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
        "VALUES (?, ?, 'worker-1', 'Deliver exact text.', 80, '2099-01-01T23:59:30Z')",
        (selected_bid_id, task_id),
    )
    connection.execute(
        "INSERT INTO board_task_contracts (task_id, lifecycle_version, "
        "timing_contract_version, acceptance_window_seconds, bidding_cutoff_us, "
        "acceptance_cutoff_us, execution_cutoff_us, review_cutoff_us, accepted_reward, "
        "accepted_bid_amount) VALUES (?, ?, 2, 60, ?, ?, ?, NULL, 100, 80)",
        (
            task_id,
            lifecycle_version,
            BIDDING_CUTOFF_US,
            ACCEPTANCE_CUTOFF_US,
            EXECUTION_CUTOFF_US,
        ),
    )
    native_hash = binding_sha256 if binding_kind == "publication_contract" else None
    legacy_hash = binding_sha256 if binding_kind == "legacy_accepted_continuation" else None
    if native_hash is not None:
        publication_result = _canonical_json(
            {
                "event_id": creation_event,
                "acceptance_cutoff_us": ACCEPTANCE_CUTOFF_US,
                "bidding_cutoff_us": BIDDING_CUTOFF_US,
                "bidding_deadline": BIDDING_CUTOFF_TEXT,
                "publication_operation_id": PUBLICATION_OPERATION_ID,
                "task_id": task_id,
            }
        )
        connection.execute(
            "INSERT INTO board_task_publications (operation_id,publication_contract_version,"
            "task_id,canonical_request_json,request_sha256,caller_kid,actor_id,"
            "lock_operation_id,state,admitted_at_us,escrow_id,lock_request_sha256,"
            "lock_receipt_event_id,published_at_us,creation_event_id,result_json,error_json,"
            "request_protocol_version,expected_input_count,base_request_sha256,"
            "freeze_operation_id,canonical_freeze_request_json,freeze_request_sha256,"
            "input_manifest_sha256,publication_contract_sha256,inputs_frozen_at_us,"
            "publish_operation_id,canonical_publish_request_json,publish_request_sha256,"
            "publish_authorized_at_us) VALUES (?,?,?,?,?,?,?,?,'published',?,?,?,?,?,?,?,NULL,"
            "2,0,?,?,?,?,?,?,?,?,?,?,?)",
            (
                PUBLICATION_OPERATION_ID,
                1,
                task_id,
                _canonical_json(_BASE_REQUEST),
                _BASE_REQUEST_SHA256,
                BOARD_KID,
                "poster-1",
                _BASE_REQUEST["lock_operation_id"],
                NOW_US - 120_000_000,
                f"escrow-{task_id}",
                lock_request.request_sha256,
                lock_event,
                PUBLISHED_AT_US,
                creation_event,
                publication_result,
                _BASE_REQUEST_SHA256,
                FREEZE_OPERATION_ID,
                _FREEZE_REQUEST_JSON,
                _FREEZE_REQUEST_SHA256,
                _INPUT_MANIFEST_SHA256,
                NATIVE_BINDING,
                NOW_US - 90_000_000,
                PUBLISH_OPERATION_ID,
                _PUBLISH_REQUEST_JSON,
                _PUBLISH_REQUEST_SHA256,
                NOW_US - 75_000_000,
            ),
        )
        connection.execute(
            "INSERT INTO board_artifact_manifests (manifest_id, task_id, kind, actor_id, "
            "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
            "publication_operation_id,public_task_id) VALUES (?,?,'input','poster-1',1,"
            "'frozen',?,?,?, ?,NULL)",
            (
                INPUT_MANIFEST_ID,
                task_id,
                _INPUT_MANIFEST_JSON,
                _INPUT_MANIFEST_SHA256,
                NOW_US - 90_000_000,
                PUBLICATION_OPERATION_ID,
            ),
        )
    if legacy_hash is not None:
        connection.execute(
            "INSERT INTO board_legacy_artifact_observations (task_id, observation_status, "
            "canonical_observation_json, observation_sha256, observed_at_us) "
            "VALUES (?, 'verified', ?, ?, ?)",
            (task_id, _LEGACY_OBSERVATION_JSON, legacy_hash, NOW_US),
        )
    connection.execute(
        "INSERT INTO board_task_artifact_contracts (task_id, artifact_contract_version, "
        "input_provenance, accepted_binding_kind, base_request_sha256, input_manifest_id, "
        "input_manifest_sha256, publication_contract_sha256, "
        "accepted_publication_contract_sha256, legacy_acceptance_observation_sha256, "
        "legacy_acceptance_observation_status,source_publication_request_sha256,"
        "publication_operation_id,input_manifest_kind,input_actor_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            task_id,
            1 if binding_kind == "publication_contract" else 0,
            "native_v6" if binding_kind == "publication_contract" else "unknown_pre_v6",
            binding_kind,
            _BASE_REQUEST_SHA256 if native_hash is not None else None,
            INPUT_MANIFEST_ID if native_hash is not None else None,
            _INPUT_MANIFEST_SHA256 if native_hash is not None else None,
            native_hash,
            native_hash,
            legacy_hash,
            "verified" if legacy_hash is not None else None,
            None,
            PUBLICATION_OPERATION_ID if native_hash is not None else None,
            "input" if native_hash is not None else None,
            "poster-1" if native_hash is not None else None,
        ),
    )
    connection.execute(
        "INSERT INTO board_artifact_manifests (manifest_id, task_id, kind, actor_id, "
        "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
        "publication_operation_id,public_task_id) VALUES (?,?,'submission','worker-1',1,"
        "'draft',NULL,NULL,NULL,NULL,?)",
        (manifest_id, task_id, task_id),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def make_writer(
    api: V6Api,
    connection: sqlite3.Connection,
    clock: MutableClock,
    policy: Any,
) -> Any:
    """Construct the proposed writer with every authority supplied explicitly."""
    return api.writer_type(
        connection,
        clock_us=clock,
        policy=policy,
        current_task_board_caller_kid=BOARD_KID,
    )


def attempt_fence(reservation: dict[str, Any]) -> dict[str, object]:
    """Extract the complete server-issued attempt fence."""
    attempt = reservation["attempt"]
    return {
        "owner_id": attempt["owner_id"],
        "generation": attempt["generation"],
        "attempt_nonce": attempt["attempt_nonce"],
    }


def database_snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    """Capture every V6 row whose mutation is relevant to reservation safety."""
    tables = (
        "events",
        "bank_transactions",
        "bank_operation_receipts",
        "bank_operation_legs",
        "bank_escrow",
        "board_task_publications",
        "board_artifact_manifests",
        "board_artifact_uploads",
        "board_artifact_upload_attempts",
        "board_artifact_archive_attempts",
        "board_artifact_cutover",
        "board_legacy_artifact_observations",
        "board_task_artifact_contracts",
        "board_task_contracts",
        "board_bids",
        "board_tasks",
    )
    return {
        table: [
            tuple(row)
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")  # nosec B608
        ]
        for table in tables
    }
