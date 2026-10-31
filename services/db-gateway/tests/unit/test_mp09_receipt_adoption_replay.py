"""MP-09 receipt-adoption replay after legitimate later state changes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_publication_writer import TaskPublicationWriter
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_publication_requests import parse_task_publication_request
from tests.conftest import make_event
from tests.fakes.mp09_receipt_adoption_fixture import (
    BOARD_KID,
    DEADLINE_ACTOR_ID,
    ESCROW_ID,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISHED_US,
    TASK_ID,
    WORKER_ID,
    BankReadGuard,
    ReceiptAdoptionFixture,
    bank_event,
    fixed_utc_text,
    full_snapshot,
    lock_publication,
    prepare_authorized_publication,
    reconcile_publication,
    register_worker,
)
from tests.fakes.native_publication_fixture import native_publication_payload

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _operation(number: int) -> str:
    return f"op-62000000-0000-4000-8000-{number:012d}"


def _later_task_history(fixture: ReceiptAdoptionFixture) -> None:
    register_worker(fixture)
    bid_id = "bid-mp09-receipt-adoption"
    fixture.clock.now_us = PUBLISHED_US + 1_000_000
    bid = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": _operation(1),
            "task_id": TASK_ID,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": WORKER_ID,
            "arguments": {"bid_id": bid_id, "bidder_id": WORKER_ID, "amount": 90},
        }
    )
    submitted = fixture.operation_writer.claim_operation(bid, BOARD_KID)
    assert submitted["operation_state"] == "completed"

    fixture.clock.now_us = PUBLISHED_US + 2_000_000
    accept = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": _operation(2),
            "task_id": TASK_ID,
            "command": "accept_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": POSTER_ID,
            "arguments": {
                "bid_id": bid_id,
                "worker_id": WORKER_ID,
                "escrow_id": ESCROW_ID,
                "reward": 100,
                "bid_amount": 90,
            },
        }
    )
    accepted = fixture.operation_writer.claim_operation(accept, BOARD_KID)
    assert accepted["operation_state"] == "completed"
    execution_cutoff = PUBLISHED_US + 2_000_000 + 3_600_000_000

    fixture.clock.now_us = execution_cutoff
    expiry_operation_id = _operation(3)
    source_reference = f"task:{TASK_ID}:expired"
    expire = parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": expiry_operation_id,
            "task_id": TASK_ID,
            "command": "expire_execution",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "system",
            "actor_id": DEADLINE_ACTOR_ID,
            "arguments": {
                "poster_id": POSTER_ID,
                "worker_id": WORKER_ID,
                "escrow_id": ESCROW_ID,
                "reward": 100,
                "source_reference": source_reference,
                "cutoff_us": execution_cutoff,
            },
        }
    )
    claimed = fixture.operation_writer.claim_operation(expire, BOARD_KID)
    assert claimed["operation_state"] == "claimed"
    fixture.bank_writer.escrow_release(
        {
            "operation_id": expiry_operation_id,
            "escrow_id": ESCROW_ID,
            "recipient_account_id": POSTER_ID,
            "source_reference": source_reference,
            "tx_id": "tx-mp09-receipt-expiry-release",
            "resolved_at": fixed_utc_text(execution_cutoff),
            "event": bank_event(
                event_type="escrow.released",
                timestamp_us=execution_cutoff,
                task_id=TASK_ID,
                agent_id=None,
            ),
        }
    )
    finalized = fixture.operation_writer.finalize_operation(expiry_operation_id, BOARD_KID)
    assert finalized["operation_state"] == "completed"
    assert finalized["result"]["status"] == "expired"


def test_published_v2_replays_original_creation_after_real_bid_accept_and_settlement(
    tmp_path: Path,
) -> None:
    fixture = prepare_authorized_publication(tmp_path / "later-history.sqlite3")
    try:
        lock_publication(fixture)
        original = reconcile_publication(fixture)
        assert original["publication_state"] == "published"
        _later_task_history(fixture)
        before = full_snapshot(fixture.connection)
        with BankReadGuard(fixture.connection, deny=True) as guard:
            owner_view = fixture.publication_writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            replay = reconcile_publication(fixture, at_us=fixture.clock.now_us + 1)
        assert guard.reads == []
        assert owner_view == original
        assert replay == original
        assert full_snapshot(fixture.connection) == before
        assert (
            fixture.connection.execute(
                "SELECT COUNT(*) FROM board_task_publications WHERE operation_id=?",
                (PUBLICATION_ID,),
            ).fetchone()[0]
            == 1
        )
    finally:
        fixture.close()


def test_published_replay_allows_schema_valid_future_accepted_binding(tmp_path: Path) -> None:
    fixture = prepare_authorized_publication(tmp_path / "accepted-binding.sqlite3")
    try:
        lock_publication(fixture)
        original = reconcile_publication(fixture)
        fixture.connection.execute(
            "UPDATE board_task_artifact_contracts SET "
            "accepted_binding_kind='publication_contract',"
            "accepted_publication_contract_sha256=publication_contract_sha256 "
            "WHERE task_id=?",
            (TASK_ID,),
        )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with BankReadGuard(fixture.connection, deny=True) as guard:
            owner_view = fixture.publication_writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            replay = reconcile_publication(fixture, at_us=PUBLISHED_US + 1)
        assert guard.reads == []
        assert owner_view == original
        assert replay == original
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_protocol_v1_current_v5_replay_preserves_stored_bytes(tmp_path: Path) -> None:
    database = tmp_path / "v1-control.sqlite3"
    writer = DbWriter(
        db_path=str(database),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid=BOARD_KID,
    )
    poster_id = f"poster-v1-{uuid4()}"
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    try:
        writer.register_agent(
            {
                "agent_id": poster_id,
                "name": "V1 control poster",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2099-01-01T00:00:00Z",
                "event": make_event(
                    source="identity",
                    event_type="agent.registered",
                    agent_id=poster_id,
                ),
            }
        )
        writer.create_account(
            {
                "operation_id": f"op-{uuid4()}",
                "account_id": poster_id,
                "balance": 100,
                "created_at": "2099-01-01T00:00:00Z",
                "initial_credit": {
                    "tx_id": f"tx-{uuid4()}",
                    "amount": 100,
                    "reference": "initial_balance",
                    "timestamp": "2099-01-01T00:00:00Z",
                },
                "event": make_event(
                    source="central-bank",
                    event_type="account.created",
                    agent_id=poster_id,
                ),
            }
        )
        payload = native_publication_payload(
            task_id=task_id,
            poster_id=poster_id,
            title="V1 byte control",
            spec="Preserve existing publication bytes.",
            reward=100,
            bidding_deadline_seconds=60,
            acceptance_window_seconds=60,
            execution_deadline_seconds=3600,
            review_deadline_seconds=60,
        )
        request = parse_task_publication_request(payload)
        clock = [4_070_908_800_000_000]
        publications = TaskPublicationWriter(writer.connection, clock_us=lambda: clock[0])
        admitted = publications.admit(request, BOARD_KID)
        writer.escrow_lock(
            {
                "operation_id": request.lock_operation_id,
                "escrow_id": escrow_id,
                "payer_account_id": poster_id,
                "amount": 100,
                "task_id": task_id,
                "created_at": "2099-01-01T00:00:01Z",
                "tx_id": f"tx-{uuid4()}",
                "event": {
                    **make_event(
                        source="central-bank",
                        event_type="escrow.locked",
                        task_id=task_id,
                        agent_id=poster_id,
                    ),
                    "timestamp": "2099-01-01T00:00:01Z",
                },
            }
        )
        clock[0] += 2_000_000
        published = publications.reconcile(request.operation_id, BOARD_KID)
        stored_before = writer.connection.execute(
            "SELECT canonical_request_json,request_sha256,result_json,error_json "
            "FROM board_task_publications WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert stored_before is not None
        owner_view = publications.get(
            request.operation_id,
            BOARD_KID,
            expected_actor_id=poster_id,
        )
        replay = publications.reconcile(request.operation_id, BOARD_KID)
        stored_after = writer.connection.execute(
            "SELECT canonical_request_json,request_sha256,result_json,error_json "
            "FROM board_task_publications WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert admitted["request_sha256"] == request.request_sha256
        assert owner_view == published
        assert replay == published
        assert tuple(stored_after) == tuple(stored_before)
        assert tuple(stored_after) == (
            request.canonical_json,
            request.request_sha256,
            stored_before[2],
            None,
        )
    finally:
        writer.close()
