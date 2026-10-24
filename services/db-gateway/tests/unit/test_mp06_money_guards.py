"""MP-06 authoritative writer-side monetary guards and atomic rejection proofs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.money_audit import audit_money
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit


def _op(identity: str) -> str:
    return f"op-{uuid5(NAMESPACE_URL, f'gateway-money-guard:{identity}')}"


def _register(writer: DbWriter, agent_id: str) -> None:
    writer.register_agent(
        {
            "agent_id": agent_id,
            "name": agent_id,
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-10-16T00:00:00Z",
            "event": make_event(agent_id=agent_id),
        }
    )


def _account(writer: DbWriter, agent_id: str, balance: int) -> None:
    _register(writer, agent_id)
    data: dict[str, Any] = {
        "account_id": agent_id,
        "balance": balance,
        "created_at": "2026-10-16T00:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance:
        data["operation_id"] = _op(f"issuance:{agent_id}")
        data["initial_credit"] = {
            "tx_id": f"tx-initial-{agent_id}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-10-16T00:00:00Z",
        }
    writer.create_account(data)


def _lock(
    writer: DbWriter,
    payer: str,
    *,
    task_id: str,
    escrow_id: str,
    amount: object,
) -> dict[str, Any]:
    return writer.escrow_lock(
        {
            "operation_id": _op(f"lock:{payer}:{task_id}"),
            "escrow_id": escrow_id,
            "payer_account_id": payer,
            "amount": amount,
            "task_id": task_id,
            "created_at": "2026-10-16T00:05:00Z",
            "tx_id": f"tx-lock-{escrow_id}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        }
    )


def _state(writer: DbWriter) -> dict[str, object]:
    connection = writer.connection
    return {
        "accounts": [
            tuple(row)
            for row in connection.execute(
                "SELECT account_id, balance, typeof(balance) FROM bank_accounts ORDER BY account_id"
            ).fetchall()
        ],
        "transactions": [
            tuple(row)
            for row in connection.execute(
                "SELECT tx_id, account_id, type, amount, typeof(amount), balance_after, "
                "typeof(balance_after), reference FROM bank_transactions ORDER BY tx_id"
            ).fetchall()
        ],
        "escrows": [
            tuple(row)
            for row in connection.execute(
                "SELECT escrow_id, payer_account_id, amount, typeof(amount), task_id, status, "
                "resolved_at FROM bank_escrow ORDER BY escrow_id"
            ).fetchall()
        ],
        "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
    }


def _assert_atomic_error(
    writer: DbWriter,
    before: dict[str, object],
    operation: Callable[[], object],
    expected_error: str,
) -> None:
    with pytest.raises(ServiceError) as raised:
        operation()
    assert raised.value.error == expected_error
    assert _state(writer) == before
    assert not writer.connection.in_transaction


@pytest.mark.parametrize("worker_pct", [100, 60])
def test_split_rejects_nonpayer_poster_even_for_zero_leg(
    db_writer: DbWriter, worker_pct: int
) -> None:
    _account(db_writer, "a-payer", 1000)
    _account(db_writer, "a-worker", 0)
    _account(db_writer, "a-impostor", 0)
    _lock(db_writer, "a-payer", task_id="t-1", escrow_id="esc-1", amount=100)
    before = _state(db_writer)
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.escrow_split(
            {
                "operation_id": _op(f"split:impostor:{worker_pct}"),
                "escrow_id": "esc-1",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-impostor",
                "worker_pct": worker_pct,
                "source_reference": "split:impostor",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "2026-10-16T00:10:00Z",
                "event": make_event(source="bank", event_type="escrow.split"),
            }
        ),
        "payload_mismatch",
    )


def test_account_rejects_mismatched_initial_issuance(db_writer: DbWriter) -> None:
    _register(db_writer, "a-mismatch")
    before = _state(db_writer)
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.create_account(
            {
                "operation_id": _op("issuance:mismatch"),
                "account_id": "a-mismatch",
                "balance": 100,
                "created_at": "now",
                "initial_credit": {
                    "tx_id": "tx-mismatch",
                    "amount": 1,
                    "reference": "initial_balance",
                    "timestamp": "now",
                },
                "event": make_event(source="bank", event_type="account.created"),
            }
        ),
        "payload_mismatch",
    )


@pytest.mark.parametrize("value", [True, 1.5, -1, SQLITE_MAX_INTEGER + 1])
def test_account_rejects_invalid_money_types_signs_and_bounds(
    db_writer: DbWriter, value: object
) -> None:
    _register(db_writer, f"a-invalid-{type(value).__name__}-{value}")
    before = _state(db_writer)
    expected = "amount_out_of_range" if value == SQLITE_MAX_INTEGER + 1 else "invalid_amount"
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.create_account(
            {
                "account_id": f"a-invalid-{type(value).__name__}-{value}",
                "balance": value,
                "created_at": "now",
                "event": make_event(source="bank", event_type="account.created"),
            }
        ),
        expected,
    )


def test_credit_overflow_is_rejected_without_sqlite_real_promotion(db_writer: DbWriter) -> None:
    _account(db_writer, "a-max", SQLITE_MAX_INTEGER)
    before = _state(db_writer)
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.credit_account(
            {
                "operation_id": _op("credit:overflow"),
                "tx_id": "tx-overflow",
                "account_id": "a-max",
                "amount": 1,
                "reference": "overflow",
                "timestamp": "now",
                "event": make_event(source="bank", event_type="account.credited"),
            }
        ),
        "amount_out_of_range",
    )


@pytest.mark.parametrize("settlement", ["released", "split"])
@pytest.mark.parametrize("replay_amount", [100, 50], ids=["exact", "changed"])
def test_settled_escrow_lifetime_replay_is_atomic(
    db_writer: DbWriter, settlement: str, replay_amount: int
) -> None:
    _account(db_writer, "a-payer", 1000)
    _account(db_writer, "a-worker", 0)
    _lock(db_writer, "a-payer", task_id="t-life", escrow_id="esc-original", amount=100)
    if settlement == "released":
        db_writer.escrow_release(
            {
                "operation_id": _op("settle:life:released"),
                "escrow_id": "esc-original",
                "recipient_account_id": "a-worker",
                "source_reference": "settle:life:released",
                "tx_id": "tx-release",
                "resolved_at": "now",
                "event": make_event(source="bank", event_type="escrow.released"),
            }
        )
    else:
        db_writer.escrow_split(
            {
                "operation_id": _op("settle:life:split"),
                "escrow_id": "esc-original",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-payer",
                "worker_pct": 60,
                "source_reference": "settle:life:split",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "now",
                "event": make_event(source="bank", event_type="escrow.split"),
            }
        )
    before = _state(db_writer)
    if replay_amount == 100:
        result = _lock(
            db_writer,
            "a-payer",
            task_id="t-life",
            escrow_id="esc-replay",
            amount=100,
        )
        assert result["receipt"]["escrow_id"] == "esc-original"
        assert result["receipt"]["result"]["escrow_status_at_commit"] == "locked"
        assert result["current_escrow"]["status"] == settlement
        assert _state(db_writer) == before
    else:
        _assert_atomic_error(
            db_writer,
            before,
            lambda: _lock(
                db_writer,
                "a-payer",
                task_id="t-life",
                escrow_id="esc-replay",
                amount=50,
            ),
            "operation_id_conflict",
        )


def test_release_recipient_overflow_is_atomic(db_writer: DbWriter) -> None:
    _account(db_writer, "a-payer", 100)
    _account(db_writer, "a-max", SQLITE_MAX_INTEGER)
    _lock(db_writer, "a-payer", task_id="t-release", escrow_id="esc-release", amount=100)
    before = _state(db_writer)
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.escrow_release(
            {
                "operation_id": _op("settle:release-overflow"),
                "escrow_id": "esc-release",
                "recipient_account_id": "a-max",
                "source_reference": "settle:release-overflow",
                "tx_id": "tx-release-overflow",
                "resolved_at": "now",
                "event": make_event(source="bank", event_type="escrow.released"),
            }
        ),
        "amount_out_of_range",
    )


def test_split_combined_same_account_overflow_is_atomic(db_writer: DbWriter) -> None:
    _account(db_writer, "a-payer", SQLITE_MAX_INTEGER)
    _lock(db_writer, "a-payer", task_id="t-split", escrow_id="esc-split", amount=100)
    db_writer.credit_account(
        {
            "operation_id": _op("credit:refill"),
            "tx_id": "tx-refill",
            "account_id": "a-payer",
            "amount": 100,
            "reference": "refill",
            "timestamp": "now",
            "event": make_event(source="bank", event_type="account.credited"),
        }
    )
    before = _state(db_writer)
    _assert_atomic_error(
        db_writer,
        before,
        lambda: db_writer.escrow_split(
            {
                "operation_id": _op("split:same-account-overflow"),
                "escrow_id": "esc-split",
                "worker_account_id": "a-payer",
                "poster_account_id": "a-payer",
                "worker_pct": 60,
                "source_reference": "split:same-account-overflow",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "now",
                "event": make_event(source="bank", event_type="escrow.split"),
            }
        ),
        "amount_out_of_range",
    )


def test_matching_issuance_and_same_account_split_at_bound_are_valid(
    db_writer: DbWriter,
) -> None:
    _account(db_writer, "a-payer", SQLITE_MAX_INTEGER)
    _lock(db_writer, "a-payer", task_id="t-valid", escrow_id="esc-valid", amount=100)
    result = db_writer.escrow_split(
        {
            "operation_id": _op("split:same-account-bound"),
            "escrow_id": "esc-valid",
            "worker_account_id": "a-payer",
            "poster_account_id": "a-payer",
            "worker_pct": 60,
            "source_reference": "split:same-account-bound",
            "worker_tx_id": "tx-worker-valid",
            "poster_tx_id": "tx-poster-valid",
            "resolved_at": "now",
            "event": make_event(source="bank", event_type="escrow.split"),
        }
    )
    assert result["receipt"]["result"]["escrow_status_at_commit"] == "split"
    row = db_writer.connection.execute(
        "SELECT balance, typeof(balance) FROM bank_accounts WHERE account_id='a-payer'"
    ).fetchone()
    assert tuple(row) == (SQLITE_MAX_INTEGER, "integer")


@pytest.mark.parametrize("operation", ["credit", "lock", "percentage"])
@pytest.mark.parametrize("amount", [True, 1.5, "1", -1, SQLITE_MAX_INTEGER + 1])
def test_every_financial_number_is_validated_at_the_writer_boundary(
    db_writer: DbWriter, operation: str, amount: object
) -> None:
    _account(db_writer, "a-payer", 100)
    _account(db_writer, "a-worker", 0)
    _lock(db_writer, "a-payer", task_id="t-guard", escrow_id="esc-guard", amount=40)
    before = _state(db_writer)

    def attempt():
        if operation == "credit":
            return db_writer.credit_account(
                {
                    "operation_id": _op(f"invalid-credit:{amount}"),
                    "tx_id": "tx-invalid",
                    "account_id": "a-payer",
                    "amount": amount,
                    "reference": "invalid",
                    "timestamp": "now",
                    "event": make_event(),
                }
            )
        if operation == "lock":
            return _lock(db_writer, "a-payer", task_id="t-new", escrow_id="esc-new", amount=amount)
        return db_writer.escrow_split(
            {
                "operation_id": _op(f"invalid-percentage:{amount}"),
                "escrow_id": "esc-guard",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-payer",
                "worker_pct": amount,
                "source_reference": "invalid-percentage",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "now",
                "event": make_event(),
            }
        )

    expected = (
        "invalid_financial_request"
        if operation == "percentage"
        else "amount_out_of_range"
        if amount == SQLITE_MAX_INTEGER + 1
        else "invalid_amount"
    )
    _assert_atomic_error(db_writer, before, attempt, expected)


@pytest.mark.parametrize("balance,initial_amount", [(1, None), (0, 1), (1, True), (0, 0)])
def test_initial_credit_exactly_explains_balance(db_writer, balance, initial_amount):
    _register(db_writer, "a-initial")
    data = {
        "operation_id": _op(f"issuance:invalid:{balance}:{initial_amount}"),
        "account_id": "a-initial",
        "balance": balance,
        "created_at": "now",
        "event": make_event(),
    }
    if initial_amount is not None:
        data["initial_credit"] = {
            "tx_id": "tx-initial",
            "amount": initial_amount,
            "reference": "initial_balance",
            "timestamp": "now",
        }
    expected = (
        "invalid_amount"
        if isinstance(initial_amount, bool) or initial_amount == 0
        else "payload_mismatch"
    )
    _assert_atomic_error(
        db_writer, _state(db_writer), lambda: db_writer.create_account(data), expected
    )


def test_credit_replay_at_maximum_returns_original_receipt_without_arithmetic(
    db_writer: DbWriter,
) -> None:
    _account(db_writer, "a-max", SQLITE_MAX_INTEGER - 1)
    operation_id = _op("credit:max-replay")
    request = {
        "operation_id": operation_id,
        "tx_id": "tx-max-replay",
        "account_id": "a-max",
        "amount": 1,
        "reference": "max-replay",
        "timestamp": "now",
        "event": make_event(),
    }
    original = db_writer.credit_account(request)
    before = _state(db_writer)
    result = db_writer.credit_account({**request, "tx_id": "tx-candidate", "timestamp": "later"})
    assert result["receipt"] == original["receipt"]
    assert result["receipt"]["result"]["balance_after"] == SQLITE_MAX_INTEGER
    assert _state(db_writer) == before
    assert not db_writer.connection.in_transaction


def test_lock_replay_does_not_require_a_second_spendable_balance(db_writer):
    _account(db_writer, "a-payer", 100)
    original = _lock(db_writer, "a-payer", task_id="t-empty", escrow_id="esc-original", amount=100)
    before = _state(db_writer)
    replay = _lock(db_writer, "a-payer", task_id="t-empty", escrow_id="esc-retry", amount=100)
    assert original["receipt"]["result"]["escrow_status_at_commit"] == "locked"
    assert replay == original
    assert _state(db_writer) == before
    assert not db_writer.connection.in_transaction


def test_split_rolls_back_first_leg_when_combined_same_account_credit_overflows(db_writer):
    _account(db_writer, "a-payer", SQLITE_MAX_INTEGER)
    _lock(db_writer, "a-payer", task_id="t-combined", escrow_id="esc-combined", amount=100)
    db_writer.credit_account(
        {
            "operation_id": _op("credit:refill-partial"),
            "tx_id": "tx-refill-partial",
            "account_id": "a-payer",
            "amount": 20,
            "reference": "refill-partial",
            "timestamp": "now",
            "event": make_event(),
        }
    )
    _assert_atomic_error(
        db_writer,
        _state(db_writer),
        lambda: db_writer.escrow_split(
            {
                "operation_id": _op("split:combined-overflow"),
                "escrow_id": "esc-combined",
                "worker_account_id": "a-payer",
                "poster_account_id": "a-payer",
                "worker_pct": 60,
                "source_reference": "split:combined-overflow",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "now",
                "event": make_event(),
            }
        ),
        "amount_out_of_range",
    )


@pytest.mark.parametrize("worker_pct", [100, 60])
def test_nonpayer_poster_is_rejected_before_crediting(db_writer: DbWriter, worker_pct: int) -> None:
    _account(db_writer, "a-payer", 100)
    _account(db_writer, "a-worker", 0)
    _lock(db_writer, "a-payer", task_id="t-missing", escrow_id="esc-missing", amount=100)
    _assert_atomic_error(
        db_writer,
        _state(db_writer),
        lambda: db_writer.escrow_split(
            {
                "operation_id": _op(f"split:absent-poster:{worker_pct}"),
                "escrow_id": "esc-missing",
                "worker_account_id": "a-worker",
                "poster_account_id": "a-absent",
                "worker_pct": worker_pct,
                "source_reference": "split:absent-poster",
                "worker_tx_id": "tx-worker",
                "poster_tx_id": "tx-poster",
                "resolved_at": "now",
                "event": make_event(),
            }
        ),
        "payload_mismatch",
    )


def test_concurrent_credits_at_maximum_have_one_atomic_winner(db_writer, initialized_db):
    _account(db_writer, "a-bounded", SQLITE_MAX_INTEGER - 1)
    before = _state(db_writer)
    barrier = Barrier(2)
    other = DbWriter(
        initialized_db,
        5000,
        "wal",
        None,
        current_task_board_caller_kid="gateway-task-board",
    )

    def credit(writer, reference):
        barrier.wait(timeout=10)
        try:
            return writer.credit_account(
                {
                    "operation_id": _op(f"credit:bounded:{reference}"),
                    "tx_id": f"tx-{reference}",
                    "account_id": "a-bounded",
                    "amount": 1,
                    "reference": reference,
                    "timestamp": "now",
                    "event": make_event(),
                }
            )
        except ServiceError as error:
            return error.error

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(credit, writer, name)
                for writer, name in ((db_writer, "left"), (other, "right"))
            ]
            outcomes = [future.result(timeout=15) for future in futures]
        assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1
        assert outcomes.count("amount_out_of_range") == 1
        after = _state(db_writer)
        assert after["accounts"][0][1:] == (SQLITE_MAX_INTEGER, "integer")
        assert len(after["transactions"]) == len(before["transactions"]) + 1
        assert after["events"] == before["events"] + 1
        assert not db_writer.connection.in_transaction
        assert not other.connection.in_transaction
    finally:
        other.close()


def test_escrow_total_and_supply_reconcile_beyond_one_account_integer_bound(db_writer):
    reader = DbReader(db_writer.connection)
    assert reader.total_escrowed() == 0
    for index in range(2):
        account_id = f"a-large-{index}"
        _account(db_writer, account_id, SQLITE_MAX_INTEGER)
        _lock(
            db_writer,
            account_id,
            task_id=f"t-large-{index}",
            escrow_id=f"esc-large-{index}",
            amount=SQLITE_MAX_INTEGER,
        )
    before = _state(db_writer)
    total = reader.total_escrowed()
    assert type(total) is int
    assert total == 2 * SQLITE_MAX_INTEGER
    audit = audit_money(db_writer.connection)
    assert audit.financially_clean
    assert audit.total_credit_issuance == total
    assert audit.total_account_balances == 0
    assert audit.total_locked_escrow == total
    assert _state(db_writer) == before
    db_writer.escrow_release(
        {
            "operation_id": _op("settle:large-release"),
            "escrow_id": "esc-large-0",
            "recipient_account_id": "a-large-0",
            "source_reference": "settle:large-release",
            "tx_id": "tx-large-release",
            "resolved_at": "now",
            "event": make_event(),
        }
    )
    assert reader.total_escrowed() == SQLITE_MAX_INTEGER
    assert audit_money(db_writer.connection).financially_clean
