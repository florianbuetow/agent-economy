"""MP-04 receipt semantics for the synchronous ledger and its async test adapter."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from service_auth.financial_responses import parse_native_financial_envelope
from service_commons.exceptions import ServiceError

from central_bank_service.monetary import SQLITE_MAX_INTEGER
from central_bank_service.services.in_memory_ledger_store import InMemoryLedgerStore
from tests.fakes.in_memory_ledger_store import InMemoryLedgerStore as AsyncLedgerStore

pytestmark = pytest.mark.unit


def _operation_id() -> str:
    return f"op-{uuid4()}"


def _store() -> InMemoryLedgerStore:
    return InMemoryLedgerStore(db_path=f"mp04-memory-{uuid4()}")


def _dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return value


def _list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def _int(value: object) -> int:
    assert type(value) is int
    return value


def _receipt(envelope: dict[str, object]) -> dict[str, object]:
    return _dict(envelope["receipt"])


def _account(store: InMemoryLedgerStore, account_id: str) -> dict[str, object]:
    account = store.get_account(account_id)
    assert account is not None
    return account


def _assert_conflict(error: ServiceError, code: str, winner: str) -> None:
    assert (error.status_code, error.error, error.details) == (
        409,
        code,
        {"winning_operation_id": winner},
    )


def _observable_state(
    store: InMemoryLedgerStore,
    payer_id: str,
    worker_id: str,
) -> dict[str, object]:
    return {
        "payer": _account(store, payer_id),
        "worker": _account(store, worker_id),
        "payer_transactions": store.get_transactions(payer_id),
        "worker_transactions": store.get_transactions(worker_id),
        "total_escrowed": store.total_escrowed(),
        "account_count": store.count_accounts(),
    }


def _race_settlement(
    barrier: Barrier,
    store: InMemoryLedgerStore,
    kind: str,
    operation_id: str,
    escrow_id: str,
) -> dict[str, object] | ServiceError:
    barrier.wait(timeout=10)
    try:
        if kind == "release":
            return store.escrow_release(
                escrow_id,
                "a-race-worker",
                operation_id,
                "task:t-race:approved",
            )
        return store.escrow_split(
            escrow_id,
            "a-race-worker",
            40,
            "a-race-payer",
            operation_id,
            "task:t-race:ruling:r-race",
        )
    except ServiceError as error:
        return error


def test_positive_issuance_replays_an_immutable_native_receipt() -> None:
    store = _store()
    operation_id = _operation_id()

    original = store.create_account("a-issued", 25, operation_id)
    original_receipt = _receipt(original)
    original_result = _dict(original_receipt["result"])
    original_leg = _dict(_list(original_receipt["legs"])[0])
    assert original["outcome"] == "native_receipt"
    assert original["current_escrow"] is None
    assert original_receipt["operation_id"] == operation_id
    assert original_receipt["operation_kind"] == "account_issuance"
    assert original_receipt["source_reference"] == "initial_balance"
    assert original_receipt["result"] == {
        "account_id": "a-issued",
        "issued_amount": 25,
        "balance_after": 25,
    }
    assert original_leg["balance_after"] == 25
    assert (
        parse_native_financial_envelope(
            original,
            operation_id,
            "account_issuance",
            {"account_id": "a-issued", "amount": 25, "reference": "initial_balance"},
        )
        == original
    )

    original_result["balance_after"] = -1
    replay = store.create_account("a-issued", 25, operation_id)

    assert _dict(_receipt(replay)["result"])["balance_after"] == 25
    assert len(store.get_transactions("a-issued")) == 1
    assert _account(store, "a-issued")["balance"] == 25


def test_zero_balance_creation_has_no_receipt_or_operation_identity() -> None:
    store = _store()

    created = store.create_account("a-zero", 0, None)

    assert set(created) == {"account_id", "balance", "created_at"}
    assert created["balance"] == 0
    assert store.get_transactions("a-zero") == []
    with pytest.raises(ServiceError) as invalid:
        store.create_account("a-invalid", 0, _operation_id())
    assert (invalid.value.status_code, invalid.value.error) == (400, "invalid_operation_id")


def test_credit_replay_and_conflicts_preserve_the_winner_without_mutation() -> None:
    store = _store()
    store.create_account("a-credit", 0, None)
    winner = _operation_id()
    original = store.credit("a-credit", 10, "funding-one", winner)
    before = store.get_transactions("a-credit")
    assert (
        parse_native_financial_envelope(
            original,
            winner,
            "credit",
            {"account_id": "a-credit", "amount": 10, "reference": "funding-one"},
        )
        == original
    )

    replay = store.credit("a-credit", 10, "funding-one", winner)
    assert replay == original
    assert replay is not original
    assert store.get_transactions("a-credit") == before

    with pytest.raises(ServiceError) as changed_body:
        store.credit("a-credit", 11, "funding-one", winner)
    _assert_conflict(changed_body.value, "operation_id_conflict", winner)

    with pytest.raises(ServiceError) as changed_kind:
        store.escrow_lock("a-credit", 5, "t-global-id", winner)
    _assert_conflict(changed_kind.value, "operation_id_conflict", winner)

    with pytest.raises(ServiceError) as changed_identity:
        store.credit("a-credit", 10, "funding-one", _operation_id())
    _assert_conflict(
        changed_identity.value,
        "financial_operation_already_committed",
        winner,
    )
    assert _account(store, "a-credit")["balance"] == 10
    assert store.get_transactions("a-credit") == before


def test_lock_replay_retains_receipt_and_projects_current_settlement() -> None:
    store = _store()
    store.create_account("a-payer", 100, _operation_id())
    store.create_account("a-worker", 0, None)
    lock_id = _operation_id()
    locked = store.escrow_lock("a-payer", 40, "t-lock", lock_id)
    escrow_id = str(_receipt(locked)["escrow_id"])
    release_id = _operation_id()

    released = store.escrow_release(
        escrow_id,
        "a-worker",
        release_id,
        "task:t-lock:approved",
    )
    replay = store.escrow_lock("a-payer", 40, "t-lock", lock_id)

    assert (
        parse_native_financial_envelope(
            locked,
            lock_id,
            "escrow_lock",
            {"payer_account_id": "a-payer", "task_id": "t-lock", "amount": 40},
        )
        == locked
    )
    assert (
        parse_native_financial_envelope(
            released,
            release_id,
            "escrow_release",
            {
                "escrow_id": escrow_id,
                "recipient_account_id": "a-worker",
                "source_reference": "task:t-lock:approved",
            },
        )
        == released
    )
    replay_receipt = _receipt(replay)
    replay_projection = _dict(replay["current_escrow"])
    released_receipt = _receipt(released)
    assert replay_receipt == _receipt(locked)
    assert replay_receipt["result"] == {"escrow_status_at_commit": "locked"}
    assert replay_projection["status"] == "released"
    assert replay_projection["resolved_at"] is not None
    assert released_receipt["operation_id"] == release_id
    assert released_receipt["result"] == {"escrow_status_at_commit": "released"}
    assert _account(store, "a-worker")["balance"] == 40


def test_opposing_settlement_reports_the_true_winner_without_moving_money() -> None:
    store = _store()
    store.create_account("a-payer", 100, _operation_id())
    store.create_account("a-worker", 0, None)
    locked = store.escrow_lock("a-payer", 50, "t-opposing", _operation_id())
    escrow_id = str(_receipt(locked)["escrow_id"])
    winner = _operation_id()
    store.escrow_release(escrow_id, "a-worker", winner, "task:t-opposing:approved")
    before_worker = store.get_account("a-worker")
    before_payer = store.get_account("a-payer")

    with pytest.raises(ServiceError) as losing_split:
        store.escrow_split(
            escrow_id,
            "a-worker",
            50,
            "a-payer",
            _operation_id(),
            "task:t-opposing:ruling:r-one",
        )

    _assert_conflict(
        losing_split.value,
        "financial_operation_already_committed",
        winner,
    )
    assert store.get_account("a-worker") == before_worker
    assert store.get_account("a-payer") == before_payer


@pytest.mark.parametrize("worker_pct", [0, 100])
def test_split_has_ordered_logical_zero_leg_without_requiring_zero_recipient(
    worker_pct: int,
) -> None:
    store = _store()
    store.create_account("a-poster", 100, _operation_id())
    if worker_pct > 0:
        store.create_account("a-worker", 0, None)
    locked = store.escrow_lock("a-poster", 100, "t-zero-leg", _operation_id())
    escrow_id = str(_receipt(locked)["escrow_id"])

    result = store.escrow_split(
        escrow_id,
        "a-worker",
        worker_pct,
        "a-poster",
        _operation_id(),
        "task:t-zero-leg:ruling:r-zero",
    )

    legs = _list(_receipt(result)["legs"])
    worker_leg, poster_leg = (_dict(legs[0]), _dict(legs[1]))
    assert (worker_leg["role"], worker_leg["amount"]) == ("worker", worker_pct)
    assert (poster_leg["role"], poster_leg["amount"]) == ("poster", 100 - worker_pct)
    zero_leg = worker_leg if worker_pct == 0 else poster_leg
    assert zero_leg["transaction_id"] is None
    assert zero_leg["balance_after"] is None
    assert _dict(result["current_escrow"])["status"] == "split"
    assert (
        parse_native_financial_envelope(
            result,
            str(_receipt(result)["operation_id"]),
            "escrow_split",
            {
                "escrow_id": escrow_id,
                "worker_account_id": "a-worker",
                "poster_account_id": "a-poster",
                "worker_pct": worker_pct,
                "source_reference": "task:t-zero-leg:ruling:r-zero",
            },
        )
        == result
    )


def test_partial_split_has_exact_positive_legs_transactions_and_conservation() -> None:
    store = _store()
    store.create_account("a-partial-payer", 100, _operation_id())
    store.create_account("a-partial-worker", 10, _operation_id())
    locked = store.escrow_lock(
        "a-partial-payer",
        80,
        "t-partial",
        _operation_id(),
    )
    escrow_id = str(_receipt(locked)["escrow_id"])
    before = _observable_state(store, "a-partial-payer", "a-partial-worker")
    operation_id = _operation_id()

    result = store.escrow_split(
        escrow_id,
        "a-partial-worker",
        25,
        "a-partial-payer",
        operation_id,
        "task:t-partial:ruling:r-partial",
    )

    receipt = _receipt(result)
    legs = _list(receipt["legs"])
    worker_leg, poster_leg = (_dict(legs[0]), _dict(legs[1]))
    payer_transactions = store.get_transactions("a-partial-payer")
    worker_transactions = store.get_transactions("a-partial-worker")
    assert _dict(before["payer"])["balance"] == 20
    assert _dict(before["worker"])["balance"] == 10
    assert (worker_leg["role"], worker_leg["amount"], worker_leg["balance_after"]) == (
        "worker",
        20,
        30,
    )
    assert (poster_leg["role"], poster_leg["amount"], poster_leg["balance_after"]) == (
        "poster",
        60,
        80,
    )
    assert worker_transactions[-1]["tx_id"] == worker_leg["transaction_id"]
    assert payer_transactions[-1]["tx_id"] == poster_leg["transaction_id"]
    assert len(worker_transactions) == len(_list(before["worker_transactions"])) + 1
    assert len(payer_transactions) == len(_list(before["payer_transactions"])) + 1
    assert _account(store, "a-partial-worker")["balance"] == 30
    assert _account(store, "a-partial-payer")["balance"] == 80
    assert 30 + 80 == 10 + 20 + 80
    assert store.total_escrowed() == 0
    assert _dict(result["current_escrow"])["status"] == "split"


def test_same_account_partial_split_overflow_preserves_all_observable_state() -> None:
    store = _store()
    store.create_account("a-same", SQLITE_MAX_INTEGER, _operation_id())
    lock_id = _operation_id()
    locked = store.escrow_lock("a-same", 2, "t-same-overflow", lock_id)
    escrow_id = str(_receipt(locked)["escrow_id"])
    store.credit("a-same", 1, "intervening-credit", _operation_id())
    before_account = _account(store, "a-same")
    before_transactions = store.get_transactions("a-same")
    before_lock_receipt = copy.deepcopy(_receipt(locked))
    failing_operation_id = _operation_id()

    with pytest.raises(ServiceError) as overflow:
        store.escrow_split(
            escrow_id,
            "a-same",
            50,
            "a-same",
            failing_operation_id,
            "task:t-same-overflow:ruling:r-overflow",
        )

    lock_replay = store.escrow_lock("a-same", 2, "t-same-overflow", lock_id)
    assert (overflow.value.status_code, overflow.value.error) == (409, "amount_out_of_range")
    assert _account(store, "a-same") == before_account
    assert store.get_transactions("a-same") == before_transactions
    assert store.count_accounts() == 1
    assert store.total_escrowed() == 2
    assert _receipt(lock_replay) == before_lock_receipt
    assert _dict(lock_replay["current_escrow"])["status"] == "locked"

    with pytest.raises(ServiceError) as identical_retry:
        store.escrow_split(
            escrow_id,
            "a-same",
            50,
            "a-same",
            failing_operation_id,
            "task:t-same-overflow:ruling:r-overflow",
        )

    second_lock_replay = store.escrow_lock("a-same", 2, "t-same-overflow", lock_id)
    assert (identical_retry.value.status_code, identical_retry.value.error) == (
        409,
        "amount_out_of_range",
    )
    assert _account(store, "a-same") == before_account
    assert store.get_transactions("a-same") == before_transactions
    assert store.count_accounts() == 1
    assert store.total_escrowed() == 2
    assert _receipt(second_lock_replay) == before_lock_receipt
    assert _dict(second_lock_replay["current_escrow"])["status"] == "locked"


def test_release_exact_replay_restores_pristine_receipt_without_new_effect() -> None:
    store = _store()
    store.create_account("a-release-payer", 50, _operation_id())
    store.create_account("a-release-worker", 0, None)
    locked = store.escrow_lock(
        "a-release-payer",
        30,
        "t-release-replay",
        _operation_id(),
    )
    escrow_id = str(_receipt(locked)["escrow_id"])
    operation_id = _operation_id()
    original = store.escrow_release(
        escrow_id,
        "a-release-worker",
        operation_id,
        "task:t-release-replay:approved",
    )
    original_receipt = _receipt(original)
    original_legs = _list(original_receipt["legs"])
    pristine_receipt = copy.deepcopy(original_receipt)
    before = _observable_state(store, "a-release-payer", "a-release-worker")
    _dict(original_receipt["result"])["escrow_status_at_commit"] = "mutated"
    _dict(original_legs[0])["transaction_id"] = "tx-mutated"

    replay = store.escrow_release(
        escrow_id,
        "a-release-worker",
        operation_id,
        "task:t-release-replay:approved",
    )

    replay_receipt = _receipt(replay)
    replay_legs = _list(replay_receipt["legs"])
    assert replay_receipt == pristine_receipt
    assert replay_receipt is not original_receipt
    assert replay_legs is not original_legs
    assert _dict(replay_legs[0]) is not _dict(original_legs[0])
    assert replay_receipt["event_id"] == pristine_receipt["event_id"]
    assert (
        _dict(replay_legs[0])["transaction_id"]
        == _dict(_list(pristine_receipt["legs"])[0])["transaction_id"]
    )
    assert _observable_state(store, "a-release-payer", "a-release-worker") == before


def test_split_exact_replay_restores_pristine_receipt_without_new_effect() -> None:
    store = _store()
    store.create_account("a-split-payer", 100, _operation_id())
    store.create_account("a-split-worker", 0, None)
    locked = store.escrow_lock(
        "a-split-payer",
        80,
        "t-split-replay",
        _operation_id(),
    )
    escrow_id = str(_receipt(locked)["escrow_id"])
    operation_id = _operation_id()
    original = store.escrow_split(
        escrow_id,
        "a-split-worker",
        25,
        "a-split-payer",
        operation_id,
        "task:t-split-replay:ruling:r-one",
    )
    original_receipt = _receipt(original)
    original_legs = _list(original_receipt["legs"])
    pristine_receipt = copy.deepcopy(original_receipt)
    before = _observable_state(store, "a-split-payer", "a-split-worker")
    _dict(original_receipt["result"])["worker_pct"] = -1
    _dict(original_legs[0])["balance_after"] = -1

    replay = store.escrow_split(
        escrow_id,
        "a-split-worker",
        25,
        "a-split-payer",
        operation_id,
        "task:t-split-replay:ruling:r-one",
    )

    replay_receipt = _receipt(replay)
    replay_legs = _list(replay_receipt["legs"])
    pristine_legs = _list(pristine_receipt["legs"])
    assert replay_receipt == pristine_receipt
    assert replay_receipt is not original_receipt
    assert replay_legs is not original_legs
    assert [_dict(leg)["transaction_id"] for leg in replay_legs] == [
        _dict(leg)["transaction_id"] for leg in pristine_legs
    ]
    assert replay_receipt["event_id"] == pristine_receipt["event_id"]
    assert _observable_state(store, "a-split-payer", "a-split-worker") == before


def test_shared_store_instances_race_to_one_settlement_receipt_winner() -> None:
    db_path = f"mp04-race-{uuid4()}"
    first = InMemoryLedgerStore(db_path)
    second = InMemoryLedgerStore(db_path)
    first.create_account("a-race-payer", 100, _operation_id())
    first.create_account("a-race-worker", 0, None)
    locked = first.escrow_lock("a-race-payer", 50, "t-race", _operation_id())
    escrow_id = str(_receipt(locked)["escrow_id"])
    before_transaction_count = len(first.get_transactions("a-race-payer")) + len(
        first.get_transactions("a-race-worker")
    )
    release_id = _operation_id()
    split_id = _operation_id()
    barrier = Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        release_future = executor.submit(
            _race_settlement,
            barrier,
            first,
            "release",
            release_id,
            escrow_id,
        )
        split_future = executor.submit(
            _race_settlement,
            barrier,
            second,
            "split",
            split_id,
            escrow_id,
        )
        outcomes = [release_future.result(timeout=15), split_future.result(timeout=15)]

    successes = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, ServiceError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    winning_receipt = _receipt(successes[0])
    winner_id = str(winning_receipt["operation_id"])
    assert winner_id in {release_id, split_id}
    _assert_conflict(conflicts[0], "financial_operation_already_committed", winner_id)
    positive_leg_count = sum(
        _int(_dict(leg)["amount"]) > 0 for leg in _list(winning_receipt["legs"])
    )
    after_transaction_count = len(first.get_transactions("a-race-payer")) + len(
        first.get_transactions("a-race-worker")
    )
    assert after_transaction_count == before_transaction_count + positive_leg_count
    assert _observable_state(first, "a-race-payer", "a-race-worker") == _observable_state(
        second,
        "a-race-payer",
        "a-race-worker",
    )
    assert (
        _int(_account(first, "a-race-payer")["balance"])
        + _int(_account(first, "a-race-worker")["balance"])
        == 100
    )
    assert first.total_escrowed() == 0


def test_failed_release_overflow_is_atomic_and_keeps_escrow_locked() -> None:
    store = _store()
    store.create_account("a-payer", 1, _operation_id())
    store.create_account("a-worker", SQLITE_MAX_INTEGER, _operation_id())
    locked = store.escrow_lock("a-payer", 1, "t-overflow", _operation_id())
    escrow_id = str(_receipt(locked)["escrow_id"])
    before_transactions = store.get_transactions("a-worker")

    with pytest.raises(ServiceError) as overflow:
        store.escrow_release(
            escrow_id,
            "a-worker",
            _operation_id(),
            "task:t-overflow:approved",
        )

    assert overflow.value.error == "amount_out_of_range"
    assert store.total_escrowed() == 1
    assert _account(store, "a-worker")["balance"] == SQLITE_MAX_INTEGER
    assert store.get_transactions("a-worker") == before_transactions


@pytest.mark.asyncio
async def test_async_fake_is_a_thin_parity_adapter_with_fresh_responses() -> None:
    store = AsyncLedgerStore(db_path=f"mp04-async-{uuid4()}")
    await store.create_account("a-async", 0, None)
    operation_id = _operation_id()

    original = await store.credit("a-async", 9, "async-credit", operation_id)
    original_leg = _dict(_list(_receipt(original)["legs"])[0])
    original_leg["balance_after"] = -1
    replay = await store.credit("a-async", 9, "async-credit", operation_id)

    replay_leg = _dict(_list(_receipt(replay)["legs"])[0])
    account = await store.get_account("a-async")
    assert account is not None
    assert replay_leg["balance_after"] == 9
    assert account["balance"] == 9
    assert await store.count_accounts() == 1
    assert await store.total_escrowed() == 0
    await store.close()
