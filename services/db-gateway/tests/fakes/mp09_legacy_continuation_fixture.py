"""Honest open-V0 cutover output for MP-09 legacy continuation tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal
from uuid import uuid4

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.bank_writer import BankWriter
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.identity_writer import IdentityWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.mp09_input_publication_fixture import open_prototype_database
from tests.fakes.mp09_receipt_adoption_fixture import full_snapshot
from tests.fakes.native_publication_fixture import publish_native_task

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

BOARD_KID: Final[str] = "gateway-task-board"
TASK_ID: Final[str] = "t-65000000-0000-4000-8000-000000000001"
POSTER_ID: Final[str] = "poster-mp09-legacy"
WORKER_ID: Final[str] = "worker-mp09-legacy"
ESCROW_ID: Final[str] = "escrow-mp09-legacy"
BID_ID: Final[str] = "bid-mp09-legacy"
BID_OPERATION_ID: Final[str] = "op-65000000-0000-4000-8000-000000000001"
ACCEPT_OPERATION_ID: Final[str] = "op-65000000-0000-4000-8000-000000000002"
CREATED_US: Final[int] = parse_utc_text_to_epoch_us("2099-01-02T00:00:00Z")
BID_US: Final[int] = CREATED_US + 30_000_000
ACCEPTED_US: Final[int] = CREATED_US + 40_000_000
BIDDING_CUTOFF_US: Final[int] = CREATED_US + 60_000_000
ACCEPTANCE_CUTOFF_US: Final[int] = CREATED_US + 120_000_000
EXECUTION_SECONDS: Final[int] = 3600
REVIEW_SECONDS: Final[int] = 60
REWARD: Final[int] = 100
BID_AMOUNT: Final[int] = 90

type LegacyWriteTarget = Literal[
    "task_update",
    "timing_update",
    "observation_insert",
    "contract_update",
    "event_insert",
    "operation_insert",
]
type LegacyFailMode = Literal["operational_before", "runtime_before", "zero_row_after"]

_MARKERS: Final[dict[LegacyWriteTarget, tuple[str, str]]] = {
    "task_update": ("UPDATE board_tasks", "status='accepted'"),
    "timing_update": ("UPDATE board_task_contracts", "accepted_reward"),
    "observation_insert": ("INSERT INTO board_legacy_artifact_observations", "VALUES"),
    "contract_update": ("UPDATE board_task_artifact_contracts", "legacy_acceptance"),
    "event_insert": ("INSERT INTO events", "VALUES"),
    "operation_insert": ("INSERT INTO board_task_operations", "VALUES"),
}


@dataclass(slots=True)
class MutableClock:
    now_us: int

    def __call__(self) -> int:
        return self.now_us


@dataclass(slots=True)
class _Owner:
    connection: sqlite3.Connection


@dataclass(slots=True)
class LegacyContinuationFixture:
    connection: sqlite3.Connection
    clock: MutableClock
    identity_writer: IdentityWriter
    bank_writer: BankWriter
    operation_writer: TaskOperationWriter
    source_publication_request_sha256: str | None
    bid_request: TaskOperationRequest | None = None
    bid_envelope: dict[str, Any] | None = None
    accept_request: TaskOperationRequest | None = None
    preserved_v1_publication: tuple[object, ...] | None = None
    preserved_v1_graph: tuple[object, ...] | None = None

    def close(self) -> None:
        self.connection.close()


@dataclass(slots=True)
class LegacyReplayReadGuard(AbstractContextManager["LegacyReplayReadGuard"]):
    connection: sqlite3.Connection
    reads: list[str]

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.reads = []

    def __enter__(self) -> LegacyReplayReadGuard:
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
        allowed = {"board_task_publications", "board_task_operations"}
        forbidden = table not in allowed and not table.startswith("sqlite_")
        return sqlite3.SQLITE_DENY if forbidden else sqlite3.SQLITE_OK


class _FailCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        target: LegacyWriteTarget,
        mode: LegacyFailMode,
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
    ) -> _FailCursor:
        normalized = " ".join(sql.split())
        prefix, marker = _MARKERS[self._target]
        selected = not self._fired and normalized.startswith(prefix) and marker in normalized
        if selected and self._mode == "operational_before":
            self._fired = True
            raise sqlite3.OperationalError(f"injected {self._target}")
        if selected and self._mode == "runtime_before":
            self._fired = True
            raise RuntimeError(f"injected {self._target}")
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


class _FailConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        target: LegacyWriteTarget,
        mode: LegacyFailMode,
    ) -> None:
        self._connection = connection
        self._target = target
        self._mode = mode

    def cursor(self) -> _FailCursor:
        return _FailCursor(self._connection.cursor(), target=self._target, mode=self._mode)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _event(
    source: str,
    event_type: str,
    timestamp_us: int,
    *,
    task_id: str | None,
    agent_id: str | None,
) -> dict[str, object]:
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": epoch_us_to_utc_text(timestamp_us),
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": event_type,
        "payload": "{}",
    }


def _v1_referenced_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    queries = (
        ("SELECT * FROM identity_agents WHERE agent_id=?", (POSTER_ID,)),
        ("SELECT * FROM bank_accounts WHERE account_id=?", (POSTER_ID,)),
        ("SELECT * FROM bank_escrow WHERE task_id=?", (TASK_ID,)),
        (
            "SELECT * FROM bank_transactions WHERE reference IN (?,?) ORDER BY tx_id",
            ("initial_balance", TASK_ID),
        ),
        (
            "SELECT * FROM bank_operation_receipts WHERE source_reference=? OR escrow_id=? "
            "ORDER BY operation_id",
            (TASK_ID, ESCROW_ID),
        ),
        (
            "SELECT * FROM bank_operation_legs WHERE operation_id IN "
            "(SELECT operation_id FROM bank_operation_receipts "
            "WHERE source_reference=? OR escrow_id=?) ORDER BY operation_id,leg_index",
            (TASK_ID, ESCROW_ID),
        ),
        (
            "SELECT * FROM events WHERE task_id=? OR agent_id=? ORDER BY event_id",
            (TASK_ID, POSTER_ID),
        ),
        ("SELECT * FROM board_tasks WHERE task_id=?", (TASK_ID,)),
        ("SELECT * FROM board_task_contracts WHERE task_id=?", (TASK_ID,)),
    )
    return tuple(
        tuple(tuple(row) for row in connection.execute(sql, parameters).fetchall())
        for sql, parameters in queries
    )


def open_legacy_cutover_fixture(
    database_path: Path,
    *,
    source_publication_request_sha256: str | None = None,
) -> LegacyContinuationFixture:
    """Create minimum honest open-V0 cutover output with no evidence rows."""
    connection = open_prototype_database(database_path)
    owner: Any = _Owner(connection)
    identity = IdentityWriter(owner)
    bank = BankWriter(owner)
    clock = MutableClock(CREATED_US)
    operations = TaskOperationWriter(
        connection,
        clock_us=clock,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )
    try:
        for actor in (POSTER_ID, WORKER_ID):
            identity.register_agent(
                {
                    "agent_id": actor,
                    "name": actor,
                    "public_key": f"ed25519:{actor}",
                    "registered_at": epoch_us_to_utc_text(CREATED_US - 3_000_000),
                    "event": _event(
                        "identity",
                        "agent.registered",
                        CREATED_US - 3_000_000,
                        task_id=None,
                        agent_id=actor,
                    ),
                }
            )
        bank.create_account(
            {
                "operation_id": "op-65000000-0000-4000-8000-000000000010",
                "account_id": POSTER_ID,
                "balance": REWARD,
                "created_at": epoch_us_to_utc_text(CREATED_US - 2_000_000),
                "initial_credit": {
                    "tx_id": "tx-mp09-legacy-issuance",
                    "amount": REWARD,
                    "reference": "initial_balance",
                    "timestamp": epoch_us_to_utc_text(CREATED_US - 2_000_000),
                },
                "event": _event(
                    "central-bank",
                    "account.created",
                    CREATED_US - 2_000_000,
                    task_id=None,
                    agent_id=POSTER_ID,
                ),
            }
        )
        bank.create_account(
            {
                "account_id": WORKER_ID,
                "balance": 0,
                "created_at": epoch_us_to_utc_text(CREATED_US - 2_000_000),
                "event": _event(
                    "central-bank",
                    "account.created",
                    CREATED_US - 2_000_000,
                    task_id=None,
                    agent_id=WORKER_ID,
                ),
            }
        )
        bank.escrow_lock(
            {
                "operation_id": "op-65000000-0000-4000-8000-000000000011",
                "escrow_id": ESCROW_ID,
                "payer_account_id": POSTER_ID,
                "amount": REWARD,
                "task_id": TASK_ID,
                "created_at": epoch_us_to_utc_text(CREATED_US - 1_000_000),
                "tx_id": "tx-mp09-legacy-lock",
                "event": _event(
                    "central-bank",
                    "escrow.locked",
                    CREATED_US - 1_000_000,
                    task_id=TASK_ID,
                    agent_id=POSTER_ID,
                ),
            }
        )
        connection.execute(
            "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
            "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,"
            "bidding_deadline,bid_count,escrow_pending,escrow_id,created_at) VALUES "
            "(?,?, 'Legacy continuation','Preserve exact legacy evidence.',100,'open',"
            "60,3600,60,?,0,0,?,?)",
            (
                TASK_ID,
                POSTER_ID,
                epoch_us_to_utc_text(BIDDING_CUTOFF_US),
                ESCROW_ID,
                epoch_us_to_utc_text(CREATED_US),
            ),
        )
        connection.execute(
            "INSERT INTO board_task_contracts (task_id,lifecycle_version,"
            "timing_contract_version,acceptance_window_seconds,bidding_cutoff_us,"
            "acceptance_cutoff_us,execution_cutoff_us,review_cutoff_us,accepted_reward,"
            "accepted_bid_amount) VALUES (?,0,2,60,?,?,NULL,NULL,NULL,NULL)",
            (TASK_ID, BIDDING_CUTOFF_US, ACCEPTANCE_CUTOFF_US),
        )
        connection.execute(
            "INSERT INTO board_task_artifact_contracts (task_id,artifact_contract_version,"
            "input_provenance,accepted_binding_kind,base_request_sha256,input_manifest_id,"
            "input_manifest_sha256,publication_contract_sha256,"
            "accepted_publication_contract_sha256,legacy_acceptance_observation_sha256,"
            "legacy_acceptance_observation_status,source_publication_request_sha256,"
            "publication_operation_id,input_manifest_kind,input_actor_id) "
            "VALUES (?,0,'unknown_pre_v6',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,"
            "NULL,NULL,NULL)",
            (TASK_ID, source_publication_request_sha256),
        )
        connection.commit()
        assert (
            connection.execute("SELECT COUNT(*) FROM board_artifact_manifests").fetchone()[0] == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_legacy_artifact_observations"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return LegacyContinuationFixture(
            connection,
            clock,
            identity,
            bank,
            operations,
            source_publication_request_sha256,
        )
    except BaseException:
        connection.close()
        raise


def open_preserved_v1_cutover_fixture(database_path: Path) -> LegacyContinuationFixture:
    """Compose V6 capability over a complete real V1 publication graph in place."""
    writer = DbWriter(
        db_path=str(database_path),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid=BOARD_KID,
    )
    try:
        writer.register_agent(
            {
                "agent_id": POSTER_ID,
                "name": POSTER_ID,
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": epoch_us_to_utc_text(CREATED_US - 3_000_000),
                "event": make_event(agent_id=POSTER_ID),
            }
        )
        writer.create_account(
            {
                "operation_id": "op-65000000-0000-4000-8000-000000000010",
                "account_id": POSTER_ID,
                "balance": REWARD,
                "created_at": epoch_us_to_utc_text(CREATED_US - 2_000_000),
                "initial_credit": {
                    "tx_id": "tx-mp09-legacy-issuance",
                    "amount": REWARD,
                    "reference": "initial_balance",
                    "timestamp": epoch_us_to_utc_text(CREATED_US - 2_000_000),
                },
                "event": _event(
                    "central-bank",
                    "account.created",
                    CREATED_US - 2_000_000,
                    task_id=None,
                    agent_id=POSTER_ID,
                ),
            }
        )
        publish_native_task(
            writer,
            caller_kid=BOARD_KID,
            clock_us=CREATED_US,
            task_id=TASK_ID,
            poster_id=POSTER_ID,
            escrow_id=ESCROW_ID,
            title="Legacy continuation",
            spec="Preserve exact legacy evidence.",
            reward=REWARD,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=60,
            execution_deadline_seconds=EXECUTION_SECONDS,
            review_deadline_seconds=REVIEW_SECONDS,
            lock_timestamp=epoch_us_to_utc_text(CREATED_US - 1_000_000),
        )
        publication = writer.connection.execute(
            "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
            "creation_event_id,result_json,error_json FROM board_task_publications WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert publication is not None
        preserved_publication = tuple(publication)
        preserved_graph = _v1_referenced_snapshot(writer.connection)
    finally:
        writer.close()

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        retained = connection.execute(
            "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
            "creation_event_id,result_json,error_json FROM board_task_publications WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert retained is not None and tuple(retained) == preserved_publication
        assert _v1_referenced_snapshot(connection) == preserved_graph
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        owner: Any = _Owner(connection)
        identity = IdentityWriter(owner)
        bank = BankWriter(owner)
        identity.register_agent(
            {
                "agent_id": WORKER_ID,
                "name": WORKER_ID,
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": epoch_us_to_utc_text(CREATED_US + 1),
                "event": _event(
                    "identity",
                    "agent.registered",
                    CREATED_US + 1,
                    task_id=None,
                    agent_id=WORKER_ID,
                ),
            }
        )
        bank.create_account(
            {
                "account_id": WORKER_ID,
                "balance": 0,
                "created_at": epoch_us_to_utc_text(CREATED_US + 1),
                "event": _event(
                    "central-bank",
                    "account.created",
                    CREATED_US + 1,
                    task_id=None,
                    agent_id=WORKER_ID,
                ),
            }
        )
        connection.execute(
            "INSERT INTO board_task_artifact_contracts "
            "(task_id,artifact_contract_version,input_provenance,accepted_binding_kind,"
            "base_request_sha256,input_manifest_id,input_manifest_sha256,"
            "publication_contract_sha256,accepted_publication_contract_sha256,"
            "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
            "source_publication_request_sha256,publication_operation_id,input_manifest_kind,"
            "input_actor_id) VALUES (?,0,'unknown_pre_v6',NULL,NULL,NULL,NULL,NULL,NULL,NULL,"
            "NULL,?,NULL,NULL,NULL)",
            (TASK_ID, preserved_publication[4]),
        )
        connection.commit()
        current = connection.execute(
            "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
            "creation_event_id,result_json,error_json FROM board_task_publications WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert current is not None and tuple(current) == preserved_publication
        assert _v1_referenced_snapshot(connection) == preserved_graph
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        clock = MutableClock(CREATED_US)
        operations = TaskOperationWriter(
            connection,
            clock_us=clock,
            deadline_actor_id="system-task-board-deadline",
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        return LegacyContinuationFixture(
            connection,
            clock,
            identity,
            bank,
            operations,
            str(preserved_publication[4]),
            preserved_v1_publication=preserved_publication,
            preserved_v1_graph=preserved_graph,
        )
    except BaseException:
        connection.close()
        raise


def submit_real_bid(
    fixture: LegacyContinuationFixture,
    *,
    at_us: int = BID_US,
) -> dict[str, Any]:
    fixture.clock.now_us = at_us
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": BID_OPERATION_ID,
            "task_id": TASK_ID,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": WORKER_ID,
            "arguments": {"bid_id": BID_ID, "bidder_id": WORKER_ID, "amount": BID_AMOUNT},
        }
    )
    result = fixture.operation_writer.claim_operation(request, BOARD_KID)
    fixture.bid_request = request
    fixture.bid_envelope = result
    return result


def prepare_accept_request(
    fixture: LegacyContinuationFixture,
    *,
    at_us: int = ACCEPTED_US,
    operation_id: str = ACCEPT_OPERATION_ID,
    overrides: dict[str, object] | None = None,
) -> TaskOperationRequest:
    fixture.clock.now_us = at_us
    arguments: dict[str, object] = {
        "bid_id": BID_ID,
        "worker_id": WORKER_ID,
        "escrow_id": ESCROW_ID,
        "reward": REWARD,
        "bid_amount": BID_AMOUNT,
    }
    arguments.update(overrides or {})
    request = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": TASK_ID,
            "command": "accept_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": POSTER_ID,
            "arguments": arguments,
        }
    )
    fixture.accept_request = request
    return request


def prepare_legacy_acceptance(
    database_path: Path,
    *,
    source_publication_request_sha256: str | None = None,
    bid_us: int = BID_US,
    accepted_us: int = ACCEPTED_US,
) -> LegacyContinuationFixture:
    fixture = open_legacy_cutover_fixture(
        database_path,
        source_publication_request_sha256=source_publication_request_sha256,
    )
    try:
        submit_real_bid(fixture, at_us=bid_us)
        prepare_accept_request(fixture, at_us=accepted_us)
        return fixture
    except BaseException:
        fixture.close()
        raise


def expected_verified_observation(
    fixture: LegacyContinuationFixture,
    *,
    bid_us: int = BID_US,
    accepted_us: int = ACCEPTED_US,
) -> dict[str, object]:
    execution_cutoff = accepted_us + EXECUTION_SECONDS * 1_000_000
    return {
        "format_version": 1,
        "artifact_contract_version": 0,
        "binding_kind": "legacy_accepted_continuation",
        "observation_status": "verified",
        "task_id": TASK_ID,
        "poster_id": POSTER_ID,
        "title": "Legacy continuation",
        "spec": "Preserve exact legacy evidence.",
        "reward": REWARD,
        "bidding_deadline_seconds": 60,
        "deadline_seconds": EXECUTION_SECONDS,
        "review_deadline_seconds": REVIEW_SECONDS,
        "bidding_deadline": epoch_us_to_utc_text(BIDDING_CUTOFF_US),
        "escrow_id": ESCROW_ID,
        "escrow_payer_id": POSTER_ID,
        "escrow_amount": REWARD,
        "escrow_status": "locked",
        "accepted_bid_id": BID_ID,
        "worker_id": WORKER_ID,
        "winning_bid_amount": BID_AMOUNT,
        "winning_bid_proposal": str(BID_AMOUNT),
        "winning_bid_submitted_at": epoch_us_to_utc_text(bid_us),
        "accepted_at": epoch_us_to_utc_text(accepted_us),
        "execution_cutoff_at": epoch_us_to_utc_text(execution_cutoff),
        "execution_cutoff_us": execution_cutoff,
        "timing_contract_version": 2,
        "accepted_reward": REWARD,
        "accepted_bid_amount": BID_AMOUNT,
        "lifecycle_status": "accepted",
        "lifecycle_version": 1,
        "input_provenance": "unknown_pre_v6",
        "source_publication_request_sha256": fixture.source_publication_request_sha256,
    }


def canonical_observation(value: dict[str, object]) -> tuple[str, str]:
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return rendered, hashlib.sha256(rendered.encode()).hexdigest()


def claim_acceptance(
    fixture: LegacyContinuationFixture,
    *,
    request: TaskOperationRequest | None = None,
    writer: TaskOperationWriter | None = None,
) -> dict[str, Any]:
    selected = request or fixture.accept_request
    assert selected is not None
    return (writer or fixture.operation_writer).claim_operation(selected, BOARD_KID)


def failpoint_writer(
    fixture: LegacyContinuationFixture,
    *,
    target: LegacyWriteTarget,
    mode: LegacyFailMode,
) -> TaskOperationWriter:
    return TaskOperationWriter(
        _FailConnection(fixture.connection, target=target, mode=mode),
        clock_us=fixture.clock,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )


__all__ = [
    "ACCEPTANCE_CUTOFF_US",
    "ACCEPTED_US",
    "ACCEPT_OPERATION_ID",
    "BIDDING_CUTOFF_US",
    "BID_AMOUNT",
    "BID_ID",
    "BID_US",
    "BOARD_KID",
    "POSTER_ID",
    "TASK_ID",
    "WORKER_ID",
    "LegacyContinuationFixture",
    "LegacyFailMode",
    "LegacyReplayReadGuard",
    "LegacyWriteTarget",
    "canonical_observation",
    "claim_acceptance",
    "expected_verified_observation",
    "failpoint_writer",
    "full_snapshot",
    "open_legacy_cutover_fixture",
    "open_preserved_v1_cutover_fixture",
    "prepare_accept_request",
    "prepare_legacy_acceptance",
    "submit_real_bid",
]
