"""G01 accepted-bid pricing: the accepted bid sets the price, the budget stays in escrow.

Approval and review-timeout approval pay the accepted price and refund the unused
budget in one Bank settlement. A Court ruling pays floor(price * pct / 100) and
refunds the rest of the original escrow. An exact retry adds no ledger rows.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_auth.signing import generate_keypair, public_key_to_b64
from service_commons.exceptions import ServiceError

from db_gateway_service.money_audit import audit_money
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from db_gateway_service.task_operation_requests import parse_task_operation_request
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_publication_fixture import publish_native_task
from tests.unit import test_mp07_court_operation_contract as court

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_BOARD_KID = "gateway-task-board"
_DEADLINE_ACTOR = "system-task-board-deadline"
_BIDDING_US = parse_utc_text_to_epoch_us("2099-01-01T00:00:02Z")
_REVIEW_TIMEOUT_US = parse_utc_text_to_epoch_us("2099-01-03T00:00:00Z")
_BUDGET = 100


@dataclass(frozen=True)
class _Parties:
    task_id: str
    poster_id: str
    worker_id: str
    escrow_id: str
    bid_id: str


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[DbWriter]:
    """Boot the production writers against one fresh current database."""
    with closing(
        DbWriter(
            db_path=str(tmp_path / "g01-pricing.db"),
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
            current_task_board_caller_kid=_BOARD_KID,
        )
    ) as booted:
        yield booted


def _native(writer: DbWriter, clock_us: int) -> TaskOperationWriter:
    return TaskOperationWriter(
        writer.connection,
        clock_us=lambda: clock_us,
        deadline_actor_id=_DEADLINE_ACTOR,
        platform_public_key="ed25519:g01-platform",
        current_task_board_caller_kid=_BOARD_KID,
    )


def _claim(writer: DbWriter, clock_us: int, payload: dict[str, object]) -> dict[str, Any]:
    return _native(writer, clock_us).claim_operation(
        parse_task_operation_request(payload), _BOARD_KID
    )


def _register(writer: DbWriter, agent_id: str, balance: int) -> None:
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2098-12-31T23:59:50Z",
            "event": make_event(source="identity", agent_id=agent_id),
        }
    )
    account: dict[str, object] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2098-12-31T23:59:51Z",
        "event": make_event(source="bank", event_type="account.created", agent_id=agent_id),
    }
    if balance:
        account["operation_id"] = f"op-{uuid4()}"
        account["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2098-12-31T23:59:51Z",
        }
    writer.create_account(account)


def _submitted_task(writer: DbWriter, bid_amount: int) -> _Parties:
    """Publish a 100-credit budget, bid, accept, upload and submit through the writers."""
    suffix = str(uuid4())
    parties = _Parties(
        task_id=f"t-{uuid4()}",
        poster_id=f"a-poster-{suffix}",
        worker_id=f"a-worker-{suffix}",
        escrow_id=f"esc-{uuid4()}",
        bid_id=f"bid-{uuid4()}",
    )
    _register(writer, parties.poster_id, _BUDGET)
    _register(writer, parties.worker_id, 0)
    publish_native_task(
        writer,
        caller_kid=_BOARD_KID,
        clock_us=parse_utc_text_to_epoch_us("2099-01-01T00:00:00Z"),
        task_id=parties.task_id,
        poster_id=parties.poster_id,
        escrow_id=parties.escrow_id,
        title="G01 pricing fixture",
        spec="Prove accepted-bid pricing.",
        reward=_BUDGET,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=3600,
        lock_timestamp="2098-12-31T23:59:58Z",
    )
    bid = _claim(writer, _BIDDING_US, _bid_payload(parties, bid_amount))
    assert bid["operation_state"] == "completed"
    accepted = _claim(
        writer,
        _BIDDING_US,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid4()}",
            "task_id": parties.task_id,
            "command": "accept_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": parties.poster_id,
            "arguments": {
                "bid_id": parties.bid_id,
                "worker_id": parties.worker_id,
                "escrow_id": parties.escrow_id,
                "reward": _BUDGET,
                "bid_amount": bid_amount,
            },
        },
    )
    assert accepted["result"]["accepted_price"] == bid_amount
    writer.record_asset(
        {
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid4()}",
            "task_id": parties.task_id,
            "uploader_id": parties.worker_id,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{parties.task_id}/deliverable.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2099-01-01T00:00:02Z",
            "event": make_event(source="board", event_type="asset.uploaded"),
        },
        {"status": "accepted", "worker_id": parties.worker_id},
    )
    submitted = _claim(
        writer,
        _BIDDING_US,
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid4()}",
            "task_id": parties.task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": parties.worker_id,
            "arguments": {"worker_id": parties.worker_id},
        },
    )
    assert submitted["operation_state"] == "completed"
    return parties


def _bid_payload(parties: _Parties, amount: int) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "operation_id": f"op-{uuid4()}",
        "task_id": parties.task_id,
        "command": "submit_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": parties.worker_id,
        "arguments": {
            "bid_id": parties.bid_id,
            "bidder_id": parties.worker_id,
            "amount": amount,
        },
    }


def _approval_payload(parties: _Parties, *, timeout: bool, writer: DbWriter) -> dict[str, Any]:
    arguments: dict[str, object] = {
        "poster_id": parties.poster_id,
        "worker_id": parties.worker_id,
        "escrow_id": parties.escrow_id,
        "reward": _BUDGET,
        "source_reference": f"task:{parties.task_id}:approved",
    }
    if timeout:
        cutoff = writer.connection.execute(
            "SELECT review_cutoff_us FROM board_task_contracts WHERE task_id=?",
            (parties.task_id,),
        ).fetchone()[0]
        arguments["cutoff_us"] = cutoff
    return {
        "protocol_version": 1,
        "operation_id": f"op-{uuid4()}",
        "task_id": parties.task_id,
        "command": "auto_approve" if timeout else "approve_task",
        "expected_status": "submitted",
        "expected_lifecycle_version": 2,
        "actor_kind": "system" if timeout else "agent",
        "actor_id": _DEADLINE_ACTOR if timeout else parties.poster_id,
        "arguments": arguments,
    }


def _settle(writer: DbWriter, pending: dict[str, Any]) -> dict[str, Any]:
    """Deliver the stored settlement intent exactly as Central Bank forwards it."""
    financial = pending["effect_intent"]["financial_request"]
    return writer.escrow_split(
        {
            "operation_id": pending["operation_id"],
            "escrow_id": financial["escrow_id"],
            "worker_account_id": financial["worker_account_id"],
            "poster_account_id": financial["poster_account_id"],
            "worker_amount": financial["worker_amount"],
            "source_reference": financial["source_reference"],
            "worker_tx_id": f"tx-{uuid4()}",
            "poster_tx_id": f"tx-{uuid4()}",
            "resolved_at": "2099-01-01T00:00:09Z",
            "event": make_event(source="central-bank", event_type="escrow.split"),
        }
    )


def _balance(writer: DbWriter, account_id: str) -> int:
    row = writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def _ledger_row_count(writer: DbWriter) -> int:
    return int(
        writer.connection.execute(
            "SELECT (SELECT COUNT(*) FROM bank_transactions)"
            " + (SELECT COUNT(*) FROM bank_operation_receipts)"
            " + (SELECT COUNT(*) FROM bank_operation_legs)"
        ).fetchone()[0]
    )


def _assert_conserved(writer: DbWriter) -> None:
    locked = writer.connection.execute(
        "SELECT COALESCE(SUM(amount),0) FROM bank_escrow WHERE status='locked'"
    ).fetchone()[0]
    balances = writer.connection.execute("SELECT SUM(balance) FROM bank_accounts").fetchone()[0]
    assert int(balances) + int(locked) == _BUDGET
    assert audit_money(writer.connection).financially_clean
    assert audit_receipt_ownership(writer.connection).ownership_clean
    assert audit_task_contracts(writer.connection).contracts_clean


@pytest.mark.parametrize("timeout", [False, True], ids=["approve", "review_timeout"])
def test_budget_100_bid_60_pays_60_and_refunds_40(writer: DbWriter, *, timeout: bool) -> None:
    """Approval and review-timeout approval pay the accepted price, not the budget."""
    parties = _submitted_task(writer, 60)
    clock = _REVIEW_TIMEOUT_US if timeout else _BIDDING_US
    pending = _claim(writer, clock, _approval_payload(parties, timeout=timeout, writer=writer))

    financial = pending["effect_intent"]["financial_request"]
    assert pending["effect_kind"] == "bank_split"
    assert financial["operation_kind"] == "escrow_split"
    assert financial["worker_amount"] == 60
    assert pending["effect_intent"]["expected_escrow"]["amount"] == _BUDGET

    receipt = _settle(writer, pending)
    assert [(leg["role"], leg["amount"]) for leg in receipt["receipt"]["legs"]] == [
        ("worker", 60),
        ("poster", 40),
    ]
    rows_after_settlement = _ledger_row_count(writer)
    replay = _settle(writer, pending)
    assert replay["receipt"] == receipt["receipt"]
    assert _ledger_row_count(writer) == rows_after_settlement

    completed = _native(writer, clock).finalize_operation(pending["operation_id"], _BOARD_KID)
    assert completed["operation_state"] == "completed"
    assert completed["result"]["status"] == "approved"
    assert completed["result"]["accepted_price"] == 60
    assert completed["result"]["worker_payment"] == 60
    assert completed["result"]["poster_refund"] == 40
    assert _balance(writer, parties.worker_id) == 60
    assert _balance(writer, parties.poster_id) == 40
    _assert_conserved(writer)


def test_bid_equal_to_budget_releases_whole_escrow_to_worker(writer: DbWriter) -> None:
    """A full-budget price needs no refund leg, so the full release stays exact."""
    parties = _submitted_task(writer, _BUDGET)
    pending = _claim(writer, _BIDDING_US, _approval_payload(parties, timeout=False, writer=writer))

    assert pending["effect_kind"] == "bank_release"
    assert pending["effect_intent"]["financial_request"]["recipient_account_id"] == (
        parties.worker_id
    )


def test_settlement_amount_cannot_exceed_escrow(writer: DbWriter) -> None:
    """The Bank rejects a worker amount larger than the locked escrow."""
    parties = _submitted_task(writer, 60)
    with pytest.raises(ServiceError) as exc_info:
        writer.escrow_split(
            {
                "operation_id": f"op-{uuid4()}",
                "escrow_id": parties.escrow_id,
                "worker_account_id": parties.worker_id,
                "poster_account_id": parties.poster_id,
                "worker_amount": _BUDGET + 1,
                "source_reference": f"task:{parties.task_id}:approved",
                "worker_tx_id": f"tx-{uuid4()}",
                "poster_tx_id": f"tx-{uuid4()}",
                "resolved_at": "2099-01-01T00:00:09Z",
                "event": make_event(source="central-bank", event_type="escrow.split"),
            }
        )
    assert exc_info.value.error == "invalid_amount"
    _assert_conserved(writer)


def test_bid_above_budget_is_rejected(writer: DbWriter) -> None:
    """Bids must fit the posted budget."""
    suffix = str(uuid4())
    parties = _Parties(
        task_id=f"t-{uuid4()}",
        poster_id=f"a-poster-{suffix}",
        worker_id=f"a-worker-{suffix}",
        escrow_id=f"esc-{uuid4()}",
        bid_id=f"bid-{uuid4()}",
    )
    _register(writer, parties.poster_id, _BUDGET)
    _register(writer, parties.worker_id, 0)
    publish_native_task(
        writer,
        caller_kid=_BOARD_KID,
        clock_us=parse_utc_text_to_epoch_us("2099-01-01T00:00:00Z"),
        task_id=parties.task_id,
        poster_id=parties.poster_id,
        escrow_id=parties.escrow_id,
        title="G01 pricing fixture",
        spec="Prove accepted-bid pricing.",
        reward=_BUDGET,
        bidding_deadline_seconds=86400,
        acceptance_window_seconds=86400,
        execution_deadline_seconds=86400,
        review_deadline_seconds=3600,
        lock_timestamp="2098-12-31T23:59:58Z",
    )
    with pytest.raises(ServiceError) as exc_info:
        _claim(writer, _BIDDING_US, _bid_payload(parties, _BUDGET + 1))
    assert exc_info.value.error == "bid_exceeds_budget"
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    ("worker_pct", "worker_payment", "poster_refund"),
    [(50, 30, 70), (33, 20, 80), (100, 61, 39), (0, 0, 100)],
)
def test_ruling_pays_floor_of_accepted_price_and_refunds_rest(
    tmp_path: Path,
    worker_pct: int,
    worker_payment: int,
    poster_refund: int,
) -> None:
    """A Court percentage applies to the accepted price; the poster gets the remainder."""
    _private, public_key = generate_keypair("g01-platform", tmp_path)
    authority = court._Authority(
        agent_id=f"a-platform-{uuid4()}",
        public_key=f"ed25519:{public_key_to_b64(public_key)}",
    )
    with closing(
        DbWriter(
            db_path=str(tmp_path / f"g01-ruling-{worker_pct}.db"),
            busy_timeout_ms=5000,
            journal_mode="wal",
            schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
            current_task_board_caller_kid=_BOARD_KID,
        )
    ) as writer:
        graph = court._seed_submitted(writer, authority=authority)
        # Fixture history: the accepted bid was 61 against the 100-credit budget.
        writer.connection.execute(
            "UPDATE board_bids SET amount=61 WHERE bid_id=?", (graph.bid_id,)
        )
        writer.connection.execute(
            "UPDATE board_task_contracts SET accepted_bid_amount=61 WHERE task_id=?",
            (graph.task_id,),
        )
        writer.connection.commit()
        claim_id = f"clm-{uuid4()}"
        court._prepare_disputed_fixture(writer, graph, claim_id)
        operation_id = f"op-{uuid4()}"
        native = court._native(writer, authority)
        pending = native.claim_operation(
            court._request(
                court._ruling_payload(graph, operation_id, authority.agent_id, claim_id, worker_pct)
            ),
            _BOARD_KID,
        )
        financial = pending["effect_intent"]["financial_request"]
        if worker_payment in {0, _BUDGET}:
            assert pending["effect_kind"] == "bank_release"
        else:
            assert financial["worker_amount"] == worker_payment
            _settle(writer, pending)
            rows = _ledger_row_count(writer)
            _settle(writer, pending)
            assert _ledger_row_count(writer) == rows
            completed = native.finalize_operation(operation_id, _BOARD_KID)
            assert completed["result"]["worker_payment"] == worker_payment
            assert completed["result"]["poster_refund"] == poster_refund
            assert _balance(writer, graph.worker_id) == worker_payment
            assert _balance(writer, graph.poster_id) == poster_refund
            locked = writer.connection.execute(
                "SELECT COUNT(*) FROM bank_escrow WHERE status='locked'"
            ).fetchone()[0]
            assert locked == 0
            assert audit_task_contracts(writer.connection).contracts_clean
            assert audit_receipt_ownership(writer.connection).ownership_clean
