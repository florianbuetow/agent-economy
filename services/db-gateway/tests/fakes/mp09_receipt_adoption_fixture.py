"""Real-producer fixture for MP-09 V2 receipt adoption tests."""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import uuid4

from db_gateway_service.artifact_upload_requests import parse_artifact_policy
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.identity_writer import IdentityWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_time import (
    epoch_us_to_utc_text,
    parse_utc_text_to_epoch_us,
)
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
    admission_payload,
    freeze_payload,
    open_prototype_database,
    publish_payload,
    require_prepublication_api,
    seed_poster,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from db_gateway_service.task_publication_v2_requests import (
        InputManifestFreezeV2,
        PublicationAdmissionV2,
        PublicationPublishV2,
    )

ADMITTED_US: Final[int] = 4_070_995_080_000_000
FROZEN_US: Final[int] = 4_070_995_110_000_000
AUTHORIZED_US: Final[int] = 4_070_995_125_000_000
LOCK_COMMITTED_US: Final[int] = 4_070_995_140_000_000
PUBLISHED_US: Final[int] = 4_070_995_141_000_000
MAX_UTC_US: Final[int] = parse_utc_text_to_epoch_us("9999-12-31T23:59:59.999999Z")
ESCROW_ID: Final[str] = "escrow-mp09-receipt-adoption"
WORKER_ID: Final[str] = "worker-mp09-receipt-adoption"
DEADLINE_ACTOR_ID: Final[str] = "system-task-board-deadline"

V2_ENVELOPE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "protocol_version",
        "publication_operation_id",
        "task_id",
        "actor_id",
        "publication_state",
        "request_sha256",
        "base_request_sha256",
        "expected_input_count",
        "input_manifest_id",
        "input_manifest_sha256",
        "publication_contract_sha256",
        "inputs_frozen_at_us",
        "freeze_operation_id",
        "publish_operation_id",
        "publish_authorized_at_us",
        "lock_operation_id",
        "escrow_id",
        "lock_request_sha256",
        "lock_receipt_event_id",
        "admitted_at",
        "published_at",
        "creation_event_id",
        "result",
        "error",
    }
)

type WriteTarget = Literal[
    "task_insert",
    "timing_insert",
    "creation_event_insert",
    "artifact_contract_insert",
    "completion_update",
    "block_update",
    "commit",
]
type FailMode = Literal["operational_before", "zero_row_after", "commit_failure"]

_WRITE_PREFIXES: Final[dict[WriteTarget, str]] = {
    "task_insert": "INSERT INTO board_tasks",
    "timing_insert": "INSERT INTO board_task_contracts",
    "creation_event_insert": "INSERT INTO events",
    "artifact_contract_insert": "INSERT INTO board_task_artifact_contracts",
    "completion_update": "UPDATE board_task_publications SET state='published'",
    "block_update": "UPDATE board_task_publications SET state='blocked_integrity'",
    "commit": "",
}


@dataclass(slots=True)
class MutableClock:
    """Explicit deterministic integer-microsecond clock."""

    now_us: int

    def __call__(self) -> int:
        return self.now_us


@dataclass(slots=True)
class _ConnectionOwner:
    """Minimal local adapter required by the real BankWriter."""

    connection: sqlite3.Connection


@dataclass(slots=True)
class ReceiptAdoptionFixture:
    """Fresh prototype and real producers for one V2 publication."""

    connection: sqlite3.Connection
    api: Any
    clock: MutableClock
    publication_writer: Any
    artifact_policy: Any
    identity_writer: IdentityWriter
    bank_writer: BankWriter
    operation_writer: TaskOperationWriter
    admission_request: PublicationAdmissionV2 | None = None
    admitted: dict[str, Any] | None = None
    manifest_id: str | None = None
    freeze_request: InputManifestFreezeV2 | None = None
    frozen: dict[str, Any] | None = None
    publish_request: PublicationPublishV2 | None = None
    authorized: dict[str, Any] | None = None
    lock_receipt: dict[str, Any] | None = None

    def close(self) -> None:
        self.connection.close()


@dataclass(slots=True)
class BankReadGuard(AbstractContextManager["BankReadGuard"]):
    """Count or deny reads of Bank-owned tables on one connection."""

    connection: sqlite3.Connection
    deny: bool
    reads: list[tuple[str, str | None]] = field(default_factory=list)

    def __enter__(self) -> BankReadGuard:
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
        column: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_READ and isinstance(table, str) and table.startswith("bank_"):
            self.reads.append((table, column))
            if self.deny:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class _FailpointCursor:
    """Cursor proxy that fails one selected reconciliation write."""

    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        target: WriteTarget,
        mode: FailMode,
    ) -> None:
        self._cursor = cursor
        self._target = target
        self._mode = mode
        self._fired = False
        self._forced_zero = False

    def execute(self, sql: str, parameters: object = ()) -> _FailpointCursor:
        normalized = " ".join(sql.split())
        selected = (
            not self._fired
            and self._target != "commit"
            and normalized.startswith(_WRITE_PREFIXES[self._target])
        )
        if selected and self._mode == "operational_before":
            self._fired = True
            raise sqlite3.OperationalError(f"injected {self._target} failure")
        self._cursor.execute(sql, parameters)  # type: ignore[arg-type]
        self._forced_zero = bool(selected and self._mode == "zero_row_after")
        if selected:
            self._fired = True
        return self

    @property
    def rowcount(self) -> int:
        return 0 if self._forced_zero else self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _FailpointConnection:
    """Connection proxy for one write or commit failure during reconciliation."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        target: WriteTarget,
        mode: FailMode,
    ) -> None:
        if target == "commit" and mode != "commit_failure":
            raise ValueError("commit target requires commit_failure mode")
        if target != "commit" and mode == "commit_failure":
            raise ValueError("commit_failure mode requires commit target")
        self._connection = connection
        self._target = target
        self._mode = mode
        self._commit_fired = False

    def cursor(self) -> _FailpointCursor:
        return _FailpointCursor(
            self._connection.cursor(),
            target=self._target,
            mode=self._mode,
        )

    def commit(self) -> None:
        if self._target == "commit" and not self._commit_fired:
            self._commit_fired = True
            raise sqlite3.OperationalError("injected commit failure")
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def fixed_utc_text(value_us: int) -> str:
    """Render the approved fixed-six UTC representation."""
    rendered = epoch_us_to_utc_text(value_us)
    prefix = rendered.removesuffix("Z")
    if "." not in prefix:
        return f"{prefix}.000000Z"
    whole, fraction = prefix.split(".", 1)
    return f"{whole}.{fraction.ljust(6, '0')}Z"


def open_receipt_fixture(
    database_path: Path, *, clock_us: int = ADMITTED_US
) -> ReceiptAdoptionFixture:
    """Open the isolated prototype with real Bank and task-operation writers."""
    api = require_prepublication_api()
    connection = open_prototype_database(database_path)
    seed_poster(connection)
    clock = MutableClock(clock_us)
    policy = parse_artifact_policy(
        {
            "contract_version": 1,
            "max_file_bytes": 32,
            "max_files_per_manifest": 2,
            "max_manifest_bytes": 64,
            "reservation_ttl_seconds": 300,
            "reservation_renew_before_expiry_seconds": 60,
            "max_upload_duration_seconds": 900,
        }
    )
    publication_writer = api.writer_type(
        connection,
        clock_us=clock,
        artifact_policy=policy,
        current_task_board_caller_kid=BOARD_KID,
    )
    owner = _ConnectionOwner(connection)
    identity_writer = IdentityWriter(owner)  # type: ignore[arg-type]
    bank_writer = BankWriter(owner)  # type: ignore[arg-type]
    operation_writer = TaskOperationWriter(
        connection,
        clock_us=clock,
        deadline_actor_id=DEADLINE_ACTOR_ID,
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )
    return ReceiptAdoptionFixture(
        connection,
        api,
        clock,
        publication_writer,
        policy,
        identity_writer,
        bank_writer,
        operation_writer,
    )


def failpoint_publication_writer(
    fixture: ReceiptAdoptionFixture,
    *,
    target: WriteTarget,
    mode: FailMode,
) -> Any:
    """Build a V2 writer that injects one rollback-safe persistence failure."""
    connection = _FailpointConnection(fixture.connection, target=target, mode=mode)
    return fixture.api.writer_type(
        connection,
        clock_us=fixture.clock,
        artifact_policy=fixture.artifact_policy,
        current_task_board_caller_kid=BOARD_KID,
    )


def bank_event(
    *,
    event_type: str,
    timestamp_us: int,
    task_id: str | None,
    agent_id: str | None,
) -> dict[str, object]:
    """Build exact Central Bank evidence used by the real writer."""
    return {
        "event_source": "central-bank",
        "event_type": event_type,
        "timestamp": fixed_utc_text(timestamp_us),
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": event_type,
        "payload": "{}",
    }


def fund_poster(fixture: ReceiptAdoptionFixture, *, balance: int = 500) -> dict[str, Any]:
    """Create the poster account and issuance receipt through BankWriter."""
    timestamp = fixed_utc_text(ADMITTED_US)
    return fixture.bank_writer.create_account(
        {
            "operation_id": "op-61000000-0000-4000-8000-000000000001",
            "account_id": POSTER_ID,
            "balance": balance,
            "created_at": timestamp,
            "initial_credit": {
                "tx_id": "tx-mp09-receipt-adoption-issuance",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": timestamp,
            },
            "event": bank_event(
                event_type="account.created",
                timestamp_us=ADMITTED_US,
                task_id=None,
                agent_id=POSTER_ID,
            ),
        }
    )


def register_worker(fixture: ReceiptAdoptionFixture) -> None:
    """Register the later worker and create its real zero-balance Bank account."""
    timestamp = fixed_utc_text(ADMITTED_US)
    fixture.identity_writer.register_agent(
        {
            "agent_id": WORKER_ID,
            "name": "MP-09 receipt worker",
            "public_key": "ed25519:mp09-receipt-worker",
            "registered_at": timestamp,
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": timestamp,
                "task_id": None,
                "agent_id": WORKER_ID,
                "summary": "Registered MP-09 receipt worker",
                "payload": "{}",
            },
        }
    )
    fixture.bank_writer.create_account(
        {
            "account_id": WORKER_ID,
            "balance": 0,
            "created_at": timestamp,
            "event": bank_event(
                event_type="account.created",
                timestamp_us=ADMITTED_US,
                task_id=None,
                agent_id=WORKER_ID,
            ),
        }
    )


def admit_publication(
    fixture: ReceiptAdoptionFixture,
    *,
    at_us: int = ADMITTED_US,
    expected_input_count: int = 0,
    overrides: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Admit one exact V2 publication through its real parser and writer."""
    fixture.clock.now_us = at_us
    payload = admission_payload(expected_input_count=expected_input_count, **(overrides or {}))
    request = fixture.api.requests.parse_publication_admission_v2(payload)
    result = fixture.publication_writer.admit(request, verified_caller_kid=BOARD_KID)
    manifest_id = result["input_manifest_id"]
    assert isinstance(manifest_id, str) and manifest_id
    fixture.admission_request = request
    fixture.admitted = result
    fixture.manifest_id = manifest_id
    return result


def freeze_publication(
    fixture: ReceiptAdoptionFixture,
    *,
    at_us: int = FROZEN_US,
) -> dict[str, Any]:
    """Freeze the zero-input manifest through the real V2 producer."""
    assert fixture.manifest_id is not None
    fixture.clock.now_us = at_us
    request = fixture.api.requests.parse_input_manifest_freeze_v2(
        freeze_payload(fixture.manifest_id)
    )
    result = fixture.publication_writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)
    fixture.freeze_request = request
    fixture.frozen = result
    return result


def authorize_publication(
    fixture: ReceiptAdoptionFixture,
    *,
    at_us: int = AUTHORIZED_US,
) -> dict[str, Any]:
    """Record the separate publish authorization through the real V2 producer."""
    assert fixture.frozen is not None
    contract_hash = fixture.frozen["publication_contract_sha256"]
    assert isinstance(contract_hash, str)
    fixture.clock.now_us = at_us
    request = fixture.api.requests.parse_publication_publish_v2(publish_payload(contract_hash))
    result = fixture.publication_writer.authorize_publish(
        request,
        verified_caller_kid=BOARD_KID,
    )
    fixture.publish_request = request
    fixture.authorized = result
    return result


def prepare_authorized_publication(
    database_path: Path,
    *,
    admission_overrides: dict[str, object] | None = None,
) -> ReceiptAdoptionFixture:
    """Create a funded, authorized publication with no task-specific Bank receipt."""
    fixture = open_receipt_fixture(database_path)
    try:
        fund_poster(fixture)
        admit_publication(fixture, overrides=admission_overrides)
        freeze_publication(fixture)
        authorize_publication(fixture)
        return fixture
    except BaseException:
        fixture.close()
        raise


def lock_publication(
    fixture: ReceiptAdoptionFixture,
    *,
    committed_at_us: int = LOCK_COMMITTED_US,
    escrow_id: str = ESCROW_ID,
) -> dict[str, Any]:
    """Persist the exact publication lock via the real BankWriter."""
    assert fixture.admission_request is not None
    request = fixture.admission_request
    timestamp = fixed_utc_text(committed_at_us)
    receipt = fixture.bank_writer.escrow_lock(
        {
            "operation_id": request.lock_operation_id,
            "escrow_id": escrow_id,
            "payer_account_id": request.actor_id,
            "amount": request.reward,
            "task_id": request.task_id,
            "created_at": timestamp,
            "tx_id": f"tx-lock-{uuid4()}",
            "event": bank_event(
                event_type="escrow.locked",
                timestamp_us=committed_at_us,
                task_id=request.task_id,
                agent_id=request.actor_id,
            ),
        }
    )
    fixture.lock_receipt = receipt
    return receipt


def reconcile_publication(
    fixture: ReceiptAdoptionFixture,
    *,
    at_us: int = PUBLISHED_US,
    caller_kid: str = BOARD_KID,
) -> dict[str, Any]:
    """Run one V2 receipt-adoption attempt at an explicit clock value."""
    fixture.clock.now_us = at_us
    return fixture.publication_writer.reconcile(
        PUBLICATION_ID,
        verified_caller_kid=caller_kid,
    )


def publication_row(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Return the complete V2 publication journal row."""
    row = connection.execute(
        "SELECT * FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def full_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Capture schema and every row in every present application table."""
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    data = tuple(
        (
            table,
            tuple(
                sorted(
                    (
                        tuple(row)
                        for row in connection.execute(f'SELECT * FROM "{table}"')  # nosec B608
                    ),
                    key=repr,
                )
            ),
        )
        for table in tables
    )
    sequence = tuple(
        tuple(row)
        for row in connection.execute("SELECT name,seq FROM sqlite_sequence ORDER BY name")
    )
    return schema, data, sequence


def bank_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Capture Bank-owned tables and their Central Bank events."""
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bank_%' ORDER BY name"
        ).fetchall()
    )
    bank_rows = tuple(
        (
            table,
            tuple(
                sorted(
                    (
                        tuple(row)
                        for row in connection.execute(f'SELECT * FROM "{table}"')  # nosec B608
                    ),
                    key=repr,
                )
            ),
        )
        for table in tables
    )
    events = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM events WHERE event_source='central-bank' ORDER BY event_id"
        ).fetchall()
    )
    return bank_rows, events


def expected_creation_result(
    fixture: ReceiptAdoptionFixture,
    *,
    event_id: int,
    escrow_id: str = ESCROW_ID,
    published_at_us: int = PUBLISHED_US,
) -> dict[str, object]:
    """Build the exact immutable V1-compatible task creation snapshot."""
    assert fixture.admission_request is not None
    request = fixture.admission_request
    bidding_cutoff = published_at_us + request.bidding_deadline_seconds * 1_000_000
    acceptance_cutoff = bidding_cutoff + request.acceptance_window_seconds * 1_000_000
    return {
        "task_id": request.task_id,
        "poster_id": request.actor_id,
        "title": request.title,
        "spec": request.spec,
        "reward": request.reward,
        "bidding_deadline_seconds": request.bidding_deadline_seconds,
        "acceptance_window_seconds": request.acceptance_window_seconds,
        "execution_deadline_seconds": request.execution_deadline_seconds,
        "review_deadline_seconds": request.review_deadline_seconds,
        "status": "open",
        "escrow_id": escrow_id,
        "created_at": fixed_utc_text(published_at_us),
        "bidding_deadline": fixed_utc_text(bidding_cutoff),
        "acceptance_deadline": fixed_utc_text(acceptance_cutoff),
        "bidding_cutoff_us": bidding_cutoff,
        "acceptance_cutoff_us": acceptance_cutoff,
        "lifecycle_version": 0,
        "timing_contract_version": 2,
        "event_id": event_id,
    }


__all__ = [
    "ADMITTED_US",
    "AUTHORIZED_US",
    "BOARD_KID",
    "DEADLINE_ACTOR_ID",
    "ESCROW_ID",
    "FROZEN_US",
    "LOCK_COMMITTED_US",
    "MAX_UTC_US",
    "POSTER_ID",
    "PUBLICATION_ID",
    "PUBLISHED_US",
    "TASK_ID",
    "V2_ENVELOPE_KEYS",
    "WORKER_ID",
    "BankReadGuard",
    "FailMode",
    "MutableClock",
    "ReceiptAdoptionFixture",
    "WriteTarget",
    "admit_publication",
    "authorize_publication",
    "bank_event",
    "bank_snapshot",
    "expected_creation_result",
    "failpoint_publication_writer",
    "fixed_utc_text",
    "freeze_publication",
    "full_snapshot",
    "fund_poster",
    "lock_publication",
    "open_receipt_fixture",
    "prepare_authorized_publication",
    "publication_row",
    "reconcile_publication",
    "register_worker",
]
