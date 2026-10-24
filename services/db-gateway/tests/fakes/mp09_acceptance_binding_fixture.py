"""Real-producer fixture for MP-09 native acceptance binding."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal

from db_gateway_service.services.artifact_writer import ArtifactWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.mp09_receipt_adoption_fixture import (
    BOARD_KID,
    ESCROW_ID,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISHED_US,
    TASK_ID,
    WORKER_ID,
    MutableClock,
    ReceiptAdoptionFixture,
    bank_event,
    fixed_utc_text,
    full_snapshot,
    lock_publication,
    prepare_authorized_publication,
    reconcile_publication,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

ACCEPT_OPERATION_ID: Final[str] = "op-63000000-0000-4000-8000-000000000002"
BID_OPERATION_ID: Final[str] = "op-63000000-0000-4000-8000-000000000001"
BID_ID: Final[str] = "bid-mp09-acceptance-binding"
BID_AMOUNT: Final[int] = 90
ACCEPTED_US: Final[int] = PUBLISHED_US + 2_000_000
SUBMISSION_MANIFEST_ID: Final[str] = "manifest-mp09-acceptance-submission"
WORKER_REGISTERED_US: Final[int] = PUBLISHED_US + 500_000

ARTIFACT_TABLES: Final[frozenset[str]] = frozenset(
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

type AcceptanceWriteTarget = Literal[
    "task_update",
    "timing_update",
    "artifact_update",
    "event_insert",
    "operation_insert",
]
type AcceptanceFailMode = Literal["operational_before", "zero_row_after"]

_WRITE_MARKERS: Final[dict[AcceptanceWriteTarget, tuple[str, str]]] = {
    "task_update": ("UPDATE board_tasks", "status='accepted'"),
    "timing_update": ("UPDATE board_task_contracts", "accepted_reward"),
    "artifact_update": ("UPDATE board_task_artifact_contracts", "accepted_binding_kind"),
    "event_insert": ("INSERT INTO events", "VALUES"),
    "operation_insert": ("INSERT INTO board_task_operations", "VALUES"),
}


@dataclass(slots=True)
class AcceptanceBindingFixture:
    """Published V2 task with one real bid and an unclaimed accept request."""

    publication: ReceiptAdoptionFixture
    published_envelope: dict[str, Any]
    bid_request: TaskOperationRequest
    bid_envelope: dict[str, Any]
    accept_request: TaskOperationRequest
    accepted_binding_sha256: str

    @property
    def connection(self) -> sqlite3.Connection:
        return self.publication.connection

    @property
    def clock(self) -> MutableClock:
        return self.publication.clock

    @property
    def operation_writer(self) -> TaskOperationWriter:
        return self.publication.operation_writer

    def close(self) -> None:
        self.publication.close()


@dataclass(slots=True)
class AcceptanceReadGuard(AbstractContextManager["AcceptanceReadGuard"]):
    """Record replay reads and deny lifecycle or artifact rereads."""

    connection: sqlite3.Connection
    deny_current_state: bool
    reads: list[str] = field(default_factory=list)

    def __enter__(self) -> AcceptanceReadGuard:
        self.connection.set_authorizer(self._authorize)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.connection.set_authorizer(None)
        return False

    def _authorize(
        self,
        action: int,
        table: str | None,
        _column: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action != sqlite3.SQLITE_READ or not isinstance(table, str):
            return sqlite3.SQLITE_OK
        self.reads.append(table)
        forbidden = {"board_tasks", "board_task_contracts", *ARTIFACT_TABLES}
        if self.deny_current_state and table in forbidden:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class _AcceptanceFailpointCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        target: AcceptanceWriteTarget,
        mode: AcceptanceFailMode,
    ) -> None:
        self._cursor = cursor
        self._target = target
        self._mode = mode
        self._fired = False
        self._zero = False

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | dict[str, object] = (),
    ) -> _AcceptanceFailpointCursor:
        normalized = " ".join(sql.split())
        prefix, marker = _WRITE_MARKERS[self._target]
        selected = not self._fired and normalized.startswith(prefix) and marker in normalized
        if selected and self._mode == "operational_before":
            self._fired = True
            raise sqlite3.OperationalError(f"injected {self._target} failure")
        self._cursor.execute(sql, parameters)
        self._zero = bool(selected and self._mode == "zero_row_after")
        if selected:
            self._fired = True
        return self

    @property
    def rowcount(self) -> int:
        return 0 if self._zero else self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _AcceptanceFailpointConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        target: AcceptanceWriteTarget,
        mode: AcceptanceFailMode,
    ) -> None:
        self._connection = connection
        self._target = target
        self._mode = mode

    def cursor(self) -> _AcceptanceFailpointCursor:
        return _AcceptanceFailpointCursor(
            self._connection.cursor(),
            target=self._target,
            mode=self._mode,
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _operation_writer(connection: Any, fixture: ReceiptAdoptionFixture) -> TaskOperationWriter:
    return TaskOperationWriter(
        connection,
        clock_us=fixture.clock,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )


def acceptance_payload(
    *,
    operation_id: str = ACCEPT_OPERATION_ID,
    worker_id: str = WORKER_ID,
    bid_id: str = BID_ID,
    bid_amount: int = BID_AMOUNT,
) -> dict[str, object]:
    """Return the unchanged exact protocol-1 accept-bid intent."""
    return {
        "protocol_version": 1,
        "operation_id": operation_id,
        "task_id": TASK_ID,
        "command": "accept_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": POSTER_ID,
        "arguments": {
            "bid_id": bid_id,
            "worker_id": worker_id,
            "escrow_id": ESCROW_ID,
            "reward": 100,
            "bid_amount": bid_amount,
        },
    }


def _register_worker_ordered(publication: ReceiptAdoptionFixture) -> None:
    timestamp = fixed_utc_text(WORKER_REGISTERED_US)
    publication.identity_writer.register_agent(
        {
            "agent_id": WORKER_ID,
            "name": "MP-09 acceptance worker",
            "public_key": "ed25519:mp09-acceptance-worker",
            "registered_at": timestamp,
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": timestamp,
                "task_id": None,
                "agent_id": WORKER_ID,
                "summary": "Registered MP-09 acceptance worker",
                "payload": "{}",
            },
        }
    )
    publication.bank_writer.create_account(
        {
            "account_id": WORKER_ID,
            "balance": 0,
            "created_at": timestamp,
            "event": bank_event(
                event_type="account.created",
                timestamp_us=WORKER_REGISTERED_US,
                task_id=None,
                agent_id=WORKER_ID,
            ),
        }
    )


def prepare_native_acceptance(database_path: Path) -> AcceptanceBindingFixture:
    """Build receipt-backed publication and bid using only real producers."""
    publication = prepare_authorized_publication(database_path)
    try:
        lock_publication(publication)
        published = reconcile_publication(publication)
        assert published["publication_state"] == "published"
        _register_worker_ordered(publication)
        publication.clock.now_us = PUBLISHED_US + 1_000_000
        bid_request = parse_task_operation_request(
            {
                "protocol_version": 1,
                "operation_id": BID_OPERATION_ID,
                "task_id": TASK_ID,
                "command": "submit_bid",
                "expected_status": "open",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": WORKER_ID,
                "arguments": {
                    "bid_id": BID_ID,
                    "bidder_id": WORKER_ID,
                    "amount": BID_AMOUNT,
                },
            }
        )
        bid_envelope = publication.operation_writer.claim_operation(bid_request, BOARD_KID)
        assert bid_envelope["operation_state"] == "completed"
        publication.clock.now_us = ACCEPTED_US
        accept_request = parse_task_operation_request(acceptance_payload())
        contract = publication.connection.execute(
            "SELECT publication_contract_sha256,accepted_binding_kind,"
            "accepted_publication_contract_sha256 FROM board_task_artifact_contracts "
            "WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert contract is not None
        assert contract[0] == published["publication_contract_sha256"]
        assert tuple(contract[1:]) == (None, None)
        task = publication.connection.execute(
            "SELECT status,lifecycle_version,bid_count FROM board_tasks "
            "JOIN board_task_contracts USING(task_id) WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert task is not None and tuple(task) == ("open", 0, 1)
        worker_events = publication.connection.execute(
            "SELECT timestamp FROM events WHERE agent_id=? "
            "AND event_source IN ('identity','central-bank') ORDER BY event_id",
            (WORKER_ID,),
        ).fetchall()
        assert [str(row[0]) for row in worker_events] == [
            fixed_utc_text(WORKER_REGISTERED_US),
            fixed_utc_text(WORKER_REGISTERED_US),
        ]
        bid_event = publication.connection.execute(
            "SELECT timestamp FROM events WHERE event_type='task.bid_submitted' AND task_id=?",
            (TASK_ID,),
        ).fetchone()
        if bid_event is None:
            bid_event = publication.connection.execute(
                "SELECT timestamp FROM events WHERE event_type='bid.submitted' AND task_id=?",
                (TASK_ID,),
            ).fetchone()
        assert bid_event is not None
        assert parse_utc_text_to_epoch_us(str(bid_event[0])) == PUBLISHED_US + 1_000_000
        assert PUBLISHED_US < WORKER_REGISTERED_US < PUBLISHED_US + 1_000_000
        assert publication.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return AcceptanceBindingFixture(
            publication,
            published,
            bid_request,
            bid_envelope,
            accept_request,
            str(contract[0]),
        )
    except BaseException:
        publication.close()
        raise


def claim_acceptance(
    fixture: AcceptanceBindingFixture,
    *,
    request: TaskOperationRequest | None = None,
    writer: TaskOperationWriter | None = None,
) -> dict[str, Any]:
    """Claim one acceptance using the current or injected writer."""
    return (writer or fixture.operation_writer).claim_operation(
        request or fixture.accept_request,
        BOARD_KID,
    )


def failpoint_operation_writer(
    fixture: AcceptanceBindingFixture,
    *,
    target: AcceptanceWriteTarget,
    mode: AcceptanceFailMode,
) -> TaskOperationWriter:
    """Build a writer with one rollback-safe acceptance failpoint."""
    connection = _AcceptanceFailpointConnection(
        fixture.connection,
        target=target,
        mode=mode,
    )
    return _operation_writer(connection, fixture.publication)


def immutable_publication_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Capture publication, input manifest, and immutable artifact provenance."""
    publication = connection.execute(
        "SELECT * FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    manifest = connection.execute(
        "SELECT * FROM board_artifact_manifests WHERE publication_operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    contract = connection.execute(
        "SELECT task_id,artifact_contract_version,input_provenance,base_request_sha256,"
        "input_manifest_id,input_manifest_sha256,publication_contract_sha256,"
        "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
        "input_actor_id FROM board_task_artifact_contracts WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    assert publication is not None and manifest is not None and contract is not None
    return tuple(publication), tuple(manifest), tuple(contract)


def install_partial_governance_surface(
    connection: sqlite3.Connection,
    *,
    missing: frozenset[str],
) -> None:
    """Create schema-only table names for a partial-surface V5 control."""
    assert missing and missing < ARTIFACT_TABLES
    for table in sorted(ARTIFACT_TABLES - missing):
        connection.execute(f'CREATE TABLE "{table}" (fixture_only INTEGER)')  # nosec B608
    connection.commit()


def coherently_tamper_frozen_manifest(connection: sqlite3.Connection) -> None:
    """Rewrite all declared hashes while leaving the authoritative upload set empty."""
    manifest = connection.execute(
        "SELECT manifest_id,task_id,actor_id FROM board_artifact_manifests "
        "WHERE publication_operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    publication = connection.execute(
        "SELECT base_request_sha256,canonical_publish_request_json FROM "
        "board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert manifest is not None and publication is not None
    canonical_manifest = json.dumps(
        {
            "format_version": 1,
            "manifest_id": str(manifest[0]),
            "task_id": str(manifest[1]),
            "kind": "input",
            "actor_id": str(manifest[2]),
            "artifacts": [
                {
                    "artifact_id": "70000000-0000-4000-8000-000000000001",
                    "display_filename": "phantom.txt",
                    "evidence_format": "text",
                    "media_type": "text/plain; charset=utf-8",
                    "byte_size": 1,
                    "sha256": "f" * 64,
                    "provenance": "v6_upload",
                    "actor_id": POSTER_ID,
                    "uploaded_at": "2099-01-01T23:59:00.000000Z",
                }
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest_hash = hashlib.sha256(canonical_manifest.encode()).hexdigest()
    contract_json = json.dumps(
        {
            "artifact_contract_version": 1,
            "base_request_sha256": str(publication[0]),
            "input_manifest_sha256": manifest_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    contract_hash = hashlib.sha256(contract_json.encode()).hexdigest()
    publish = json.loads(str(publication[1]))
    assert isinstance(publish, dict)
    publish["publication_contract_sha256"] = contract_hash
    publish_json = json.dumps(publish, sort_keys=True, separators=(",", ":"))
    publish_hash = hashlib.sha256(publish_json.encode()).hexdigest()
    connection.execute(
        "UPDATE board_artifact_manifests SET canonical_manifest_json=?,manifest_sha256=? "
        "WHERE publication_operation_id=?",
        (canonical_manifest, manifest_hash, PUBLICATION_ID),
    )
    connection.execute(
        "UPDATE board_task_publications SET input_manifest_sha256=?,"
        "publication_contract_sha256=?,canonical_publish_request_json=?,"
        "publish_request_sha256=? WHERE operation_id=?",
        (manifest_hash, contract_hash, publish_json, publish_hash, PUBLICATION_ID),
    )
    connection.execute(
        "UPDATE board_task_artifact_contracts SET input_manifest_sha256=?,"
        "publication_contract_sha256=? WHERE task_id=?",
        (manifest_hash, contract_hash, TASK_ID),
    )
    connection.commit()


def make_artifact_writer(fixture: AcceptanceBindingFixture) -> ArtifactWriter:
    """Construct the existing worker upload kernel over the accepted task."""
    return ArtifactWriter(
        fixture.connection,
        clock_us=fixture.clock,
        policy=fixture.publication.artifact_policy,
        current_task_board_caller_kid=BOARD_KID,
    )


__all__ = [
    "ACCEPTED_US",
    "ACCEPT_OPERATION_ID",
    "ARTIFACT_TABLES",
    "BID_AMOUNT",
    "BID_ID",
    "BID_OPERATION_ID",
    "SUBMISSION_MANIFEST_ID",
    "WORKER_REGISTERED_US",
    "AcceptanceBindingFixture",
    "AcceptanceFailMode",
    "AcceptanceReadGuard",
    "AcceptanceWriteTarget",
    "acceptance_payload",
    "claim_acceptance",
    "coherently_tamper_frozen_manifest",
    "failpoint_operation_writer",
    "full_snapshot",
    "immutable_publication_snapshot",
    "install_partial_governance_surface",
    "make_artifact_writer",
    "prepare_native_acceptance",
]
