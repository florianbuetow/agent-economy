"""T-122 acceptance proofs for the explicit poster-selected acceptance window.

Task Board and DB Gateway persistence are real. Deadline refunds persist and
validate the Bank receipt, ledger transaction, restored poster balance, and escrow resolution.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from threading import Lock
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.financial_identity import operation_id_for

from task_board_service.core.state import get_app_state
from tests.integration.conftest import make_jws_token, register_identity_agent

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_START = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
_BIDDING_SECONDS = 10
_DEFAULT_ACCEPTANCE_WINDOW_SECONDS = 20
_MISSING = object()


class _MutableDomainClock:
    """Single synchronized clock for Task Board lifecycle eligibility."""

    def __init__(self, initial: datetime) -> None:
        self._value = initial
        self._lock = Lock()

    def now(self, timezone: tzinfo | None) -> datetime:
        with self._lock:
            value = self._value
        if timezone is None:
            return value.replace(tzinfo=None)
        return value.astimezone(timezone)

    def set(self, value: datetime) -> None:
        with self._lock:
            if value < self._value:
                raise AssertionError("MP-07 acceptance clock cannot move backwards")
            self._value = value

    def epoch_us(self) -> int:
        with self._lock:
            value = self._value
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        delta = value - epoch
        return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class _ClockDateTime:
    """datetime parsing with current time supplied by the acceptance clock."""

    clock: _MutableDomainClock

    @classmethod
    def now(cls, timezone: tzinfo | None = None) -> datetime:
        return cls.clock.now(timezone)

    @classmethod
    def fromisoformat(cls, value: str) -> datetime:
        return datetime.fromisoformat(value)

    @classmethod
    def fromtimestamp(cls, timestamp: float, timezone: tzinfo | None = None) -> datetime:
        return datetime.fromtimestamp(timestamp, timezone)


@dataclass(frozen=True)
class _TaskSetup:
    task_id: str
    poster_id: str
    escrow_id: str
    response: dict[str, Any]


@dataclass(frozen=True)
class _BidSetup:
    task: _TaskSetup
    worker_id: str
    bid_id: str


def _install_domain_clock(monkeypatch: pytest.MonkeyPatch) -> _MutableDomainClock:
    clock = _MutableDomainClock(_START)
    _ClockDateTime.clock = clock
    for module_name in (
        "task_board_service.services.asset_manager",
        "task_board_service.services.deadline_evaluator",
        "task_board_service.services.task_db_client",
        "task_board_service.services.task_helpers",
    ):
        monkeypatch.setattr(f"{module_name}.datetime", _ClockDateTime)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock.epoch_us)
    return clock


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="auto").replace("+00:00", "Z")


def _read_rows(
    db_path: str,
    query: str,
    params: tuple[object, ...] = (),
) -> list[dict[str, object]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(query, params).fetchall()]
    finally:
        connection.close()


def _epoch_us(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _deadline_id(task_id: str, command: str, version: int, cutoff_us: int) -> str:
    return operation_id_for("task-deadline", (task_id, command, str(version), str(cutoff_us)))


def _install_persisting_release(
    bank: AsyncMock,
    gateway_client: httpx.AsyncClient,
    *,
    gateway_db_path: str,
    task_id: str,
    operation_id: str,
    command: str,
    cutoff: datetime,
    escrow_id: str,
    recipient_id: str,
) -> None:
    async def _release(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs["operation_id"] == operation_id
        assert kwargs["escrow_id"] == escrow_id
        assert kwargs["recipient_account_id"] == recipient_id
        assert _read_rows(
            gateway_db_path,
            "SELECT command,operation_state,effect_state FROM board_task_operations "
            "WHERE operation_id=?",
            (operation_id,),
        ) == [{"command": command, "operation_state": "claimed", "effect_state": "pending"}]
        resolved_at = _iso(cutoff)
        response = await gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "source_reference": kwargs["source_reference"],
                "tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": resolved_at,
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.released",
                    "timestamp": resolved_at,
                    "task_id": task_id,
                    "agent_id": recipient_id,
                    "summary": f"Released escrow for {task_id}",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["outcome"] == "native_receipt"
        return cast("dict[str, Any]", result)

    bank.escrow_release = AsyncMock(side_effect=_release)


def _assert_refund_completion(
    db_path: str,
    *,
    task_id: str,
    poster_id: str,
    escrow_id: str,
    operation_id: str,
    command: str,
    cutoff: datetime,
) -> None:
    resolved_at = _iso(cutoff)
    source_reference = f"task:{task_id}:expired"
    assert _read_rows(
        db_path,
        "SELECT e.status,e.resolved_at,a.balance,t.type,t.amount,t.balance_after,"
        "t.reference,t.timestamp FROM bank_escrow AS e JOIN bank_accounts AS a "
        "ON a.account_id=e.payer_account_id JOIN bank_transactions AS t "
        "ON t.account_id=a.account_id WHERE e.escrow_id=? AND t.type='escrow_release' "
        "AND t.reference=? AND a.account_id=?",
        (escrow_id, escrow_id, poster_id),
    ) == [
        {
            "status": "released",
            "resolved_at": resolved_at,
            "balance": 100,
            "type": "escrow_release",
            "amount": 100,
            "balance_after": 100,
            "reference": escrow_id,
            "timestamp": resolved_at,
        }
    ]
    assert _read_rows(
        db_path,
        "SELECT r.operation_kind,r.source_reference,l.role,l.account_id,l.direction,"
        "l.amount,l.balance_after FROM bank_operation_receipts AS r "
        "JOIN bank_operation_legs AS l ON l.operation_id=r.operation_id "
        "WHERE r.operation_id=?",
        (operation_id,),
    ) == [
        {
            "operation_kind": "escrow_release",
            "source_reference": source_reference,
            "role": "recipient",
            "account_id": poster_id,
            "direction": "credit",
            "amount": 100,
            "balance_after": 100,
        }
    ]
    operation = _read_rows(
        db_path,
        "SELECT o.command,o.operation_state,o.effect_state,o.event_id,"
        "e.event_type,e.timestamp FROM board_task_operations AS o JOIN events AS e "
        "ON e.event_id=o.event_id WHERE o.operation_id=?",
        (operation_id,),
    )
    assert len(operation) == 1
    assert operation[0] == {
        "command": command,
        "operation_state": "completed",
        "effect_state": "succeeded",
        "event_id": operation[0]["event_id"],
        "event_type": "task.expired",
        "timestamp": resolved_at,
    }


async def _seed_funded_escrow(
    gateway_client: httpx.AsyncClient,
    *,
    payer_id: str,
    amount: int,
    task_id: str,
    escrow_id: str,
) -> dict[str, object]:
    await register_identity_agent(gateway_client, payer_id)
    account_response = await gateway_client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid.uuid4()}",
            "account_id": payer_id,
            "created_at": "2026-10-17T11:59:58Z",
            "balance": amount,
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T11:59:58Z",
                "agent_id": payer_id,
                "summary": f"Created account for {payer_id}",
                "payload": "{}",
            },
            "initial_credit": {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": amount,
                "reference": "initial_balance",
                "timestamp": "2026-10-17T11:59:58Z",
            },
        },
    )
    assert account_response.status_code == 201, account_response.text

    lock_response = await gateway_client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": operation_id_for("task-lock", (task_id, payer_id)),
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": "2026-10-17T11:59:59Z",
            "tx_id": f"tx-{uuid.uuid4()}",
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-10-17T11:59:59Z",
                "agent_id": payer_id,
                "summary": f"Escrow locked for {task_id}",
                "payload": "{}",
            },
        },
    )
    assert lock_response.status_code == 201, lock_response.text
    envelope = lock_response.json()
    assert isinstance(envelope, dict)
    assert envelope["outcome"] == "native_receipt"
    return envelope


async def _create_task(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    *,
    acceptance_window_seconds: object,
) -> tuple[_TaskSetup, httpx.Response, AsyncMock]:
    poster_id = f"a-mp07-window-poster-{uuid.uuid4()}"
    task_id = f"t-{uuid.uuid4()}"
    escrow_id = f"esc-{uuid.uuid4()}"
    lock_envelope = await _seed_funded_escrow(
        gateway_client,
        payer_id=poster_id,
        amount=100,
        task_id=task_id,
        escrow_id=escrow_id,
    )

    state = get_app_state()
    assert state.central_bank_client is not None
    bank = cast("AsyncMock", state.central_bank_client)
    bank.escrow_lock = AsyncMock(return_value=lock_envelope)
    bank.escrow_release.reset_mock()

    task_payload: dict[str, Any] = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": poster_id,
        "title": "MP-07 explicit acceptance window",
        "spec": "Preserve the selected acceptance window and enforce both cutoffs.",
        "reward": 100,
        "bidding_deadline_seconds": _BIDDING_SECONDS,
        "execution_deadline_seconds": 60,
        "review_deadline_seconds": 60,
    }
    if acceptance_window_seconds is not _MISSING:
        task_payload["acceptance_window_seconds"] = acceptance_window_seconds
    escrow_operation_id = operation_id_for("task-lock", (task_id, poster_id))
    response = await client.post(
        "/tasks",
        json={
            "task_token": make_jws_token(poster_key, poster_id, task_payload),
            "escrow_token": make_jws_token(
                poster_key,
                poster_id,
                {
                    "action": "escrow_lock",
                    "operation_id": escrow_operation_id,
                    "task_id": task_id,
                    "agent_id": poster_id,
                    "amount": 100,
                },
            ),
        },
    )
    response_data = response.json() if response.status_code in {200, 201} else {}
    setup = _TaskSetup(task_id, poster_id, escrow_id, response_data)
    return setup, response, bank


async def _create_task_with_bid(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
    *,
    acceptance_window_seconds: int,
) -> tuple[_BidSetup, AsyncMock]:
    task, response, bank = await _create_task(
        client,
        gateway_client,
        poster_key,
        acceptance_window_seconds=acceptance_window_seconds,
    )
    assert response.status_code == 201, response.text

    worker_id = f"a-mp07-window-worker-{uuid.uuid4()}"
    await register_identity_agent(gateway_client, worker_id)
    bid_response = await client.post(
        f"/tasks/{task.task_id}/bids",
        json={
            "token": make_jws_token(
                worker_key,
                worker_id,
                {
                    "action": "submit_bid",
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": 0,
                    "task_id": task.task_id,
                    "bidder_id": worker_id,
                    "amount": 90,
                },
            )
        },
    )
    assert bid_response.status_code == 201, bid_response.text
    return _BidSetup(task, worker_id, str(bid_response.json()["bid_id"])), bank


async def _accept_bid(
    client: httpx.AsyncClient,
    poster_key: Ed25519PrivateKey,
    bid: _BidSetup,
) -> httpx.Response:
    return await client.post(
        f"/tasks/{bid.task.task_id}/bids/{bid.bid_id}/accept",
        json={
            "token": make_jws_token(
                poster_key,
                bid.task.poster_id,
                {
                    "action": "accept_bid",
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": 0,
                    "task_id": bid.task.task_id,
                    "bid_id": bid.bid_id,
                    "poster_id": bid.task.poster_id,
                },
            )
        },
    )


@pytest.mark.parametrize(
    "acceptance_window_seconds",
    [_MISSING, None, 0, -1, True, 1.5, "20"],
    ids=["missing", "null", "zero", "negative", "bool", "float", "string"],
)
async def test_create_rejects_missing_or_invalid_acceptance_window_before_writes(
    acceptance_window_seconds: object,
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_domain_clock(monkeypatch)
    task, response, bank = await _create_task(
        client,
        gateway_client,
        poster_keypair[0],
        acceptance_window_seconds=acceptance_window_seconds,
    )
    task_rows = _read_rows(
        gateway_db_path,
        "SELECT task_id FROM board_tasks WHERE task_id = ?",
        (task.task_id,),
    )
    event_rows = _read_rows(
        gateway_db_path,
        "SELECT event_id FROM events WHERE task_id = ?",
        (task.task_id,),
    )
    diagnostics = {
        "acceptance_window_seconds": repr(acceptance_window_seconds),
        "status_code": response.status_code,
        "response": response.json(),
        "bank_lock_calls": bank.escrow_lock.await_count,
        "task_rows": task_rows,
        "event_rows": event_rows,
    }

    assert (
        response.status_code == 400
        and bank.escrow_lock.await_count == 0
        and task_rows == []
        and event_rows == []
    ), diagnostics
    expected_error = (
        "invalid_payload" if acceptance_window_seconds is _MISSING else "invalid_deadline"
    )
    assert response.json()["error"] == expected_error, diagnostics
    assert "acceptance_window_seconds" in response.json()["message"], diagnostics


async def test_two_selected_windows_are_preserved_and_expose_distinct_deadlines(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_domain_clock(monkeypatch)
    windows = (20, 45)
    observed: list[tuple[object, object, object, object, object, object]] = []
    for window in windows:
        task, response, _bank = await _create_task(
            client,
            gateway_client,
            poster_keypair[0],
            acceptance_window_seconds=window,
        )
        assert response.status_code == 201, response.text
        read_response = await client.get(f"/tasks/{task.task_id}")
        assert read_response.status_code == 200, read_response.text
        read_data = read_response.json()
        observed.append(
            (
                task.response.get("acceptance_window_seconds"),
                task.response.get("acceptance_deadline"),
                read_data.get("acceptance_window_seconds"),
                read_data.get("acceptance_deadline"),
                task.response.get("timing_contract_version"),
                read_data.get("timing_contract_version"),
            )
        )

    expected = [
        (
            window,
            _iso(_START + timedelta(seconds=_BIDDING_SECONDS + window)),
            window,
            _iso(_START + timedelta(seconds=_BIDDING_SECONDS + window)),
            2,
            2,
        )
        for window in windows
    ]
    assert observed == expected, {"observed": observed, "expected": expected}


async def test_early_acceptance_remains_legal(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_domain_clock(monkeypatch)
    bid, bank = await _create_task_with_bid(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        acceptance_window_seconds=_DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
    )
    bank.escrow_release.reset_mock()

    response = await _accept_bid(client, poster_keypair[0], bid)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert response.json()["worker_id"] == bid.worker_id
    bank.escrow_release.assert_not_awaited()
    assert _read_rows(
        gateway_db_path,
        "SELECT lifecycle_version,timing_contract_version,accepted_reward,accepted_bid_amount "
        "FROM board_task_contracts WHERE task_id=?",
        (bid.task.task_id,),
    ) == [
        {
            "lifecycle_version": 1,
            "timing_contract_version": 2,
            "accepted_reward": 100,
            "accepted_bid_amount": 90,
        }
    ]


async def test_zero_bid_task_expires_at_exact_bidding_cutoff(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    task, response, bank = await _create_task(
        client,
        gateway_client,
        poster_keypair[0],
        acceptance_window_seconds=_DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
    )
    assert response.status_code == 201, response.text
    bank.escrow_release.reset_mock()
    bidding_cutoff = _START + timedelta(seconds=_BIDDING_SECONDS)
    operation_id = _deadline_id(
        task.task_id,
        "expire_bidding",
        0,
        _epoch_us(bidding_cutoff),
    )
    _install_persisting_release(
        bank,
        gateway_client,
        gateway_db_path=gateway_db_path,
        task_id=task.task_id,
        operation_id=operation_id,
        command="expire_bidding",
        cutoff=bidding_cutoff,
        escrow_id=task.escrow_id,
        recipient_id=task.poster_id,
    )
    clock.set(bidding_cutoff)

    result = await client.get(f"/tasks/{task.task_id}")
    events = _read_rows(
        gateway_db_path,
        "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'task.expired'",
        (task.task_id,),
    )

    assert result.status_code == 200, result.text
    assert result.json()["status"] == "expired"
    assert len(events) == 1
    bank.escrow_release.assert_awaited_once()
    _assert_refund_completion(
        gateway_db_path,
        task_id=task.task_id,
        poster_id=task.poster_id,
        escrow_id=task.escrow_id,
        operation_id=operation_id,
        command="expire_bidding",
        cutoff=bidding_cutoff,
    )


async def test_new_bid_after_bidding_cutoff_rejects_without_closing_task_with_bid(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    bid, bank = await _create_task_with_bid(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        acceptance_window_seconds=_DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
    )
    second_worker_key = Ed25519PrivateKey.generate()
    second_worker_id = f"a-mp07-window-worker-{uuid.uuid4()}"
    await register_identity_agent(gateway_client, second_worker_id)
    bank.escrow_release.reset_mock()
    clock.set(_START + timedelta(seconds=_BIDDING_SECONDS))

    response = await client.post(
        f"/tasks/{bid.task.task_id}/bids",
        json={
            "token": make_jws_token(
                second_worker_key,
                second_worker_id,
                {
                    "action": "submit_bid",
                    "operation_id": f"op-{uuid.uuid4()}",
                    "expected_lifecycle_version": 0,
                    "task_id": bid.task.task_id,
                    "bidder_id": second_worker_id,
                    "amount": 80,
                },
            )
        },
    )
    rows = _read_rows(
        gateway_db_path,
        "SELECT status, bid_count, expired_at FROM board_tasks WHERE task_id = ?",
        (bid.task.task_id,),
    )
    bids = _read_rows(
        gateway_db_path,
        "SELECT bid_id FROM board_bids WHERE task_id = ? ORDER BY bid_id",
        (bid.task.task_id,),
    )
    events = _read_rows(
        gateway_db_path,
        "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'task.expired'",
        (bid.task.task_id,),
    )
    diagnostics = {
        "response_status": response.status_code,
        "response": response.json(),
        "task_rows": rows,
        "bid_rows": bids,
        "expiry_events": events,
        "bank_release_calls": bank.escrow_release.await_count,
    }

    assert response.status_code == 409, diagnostics
    assert response.json()["error"] == "cutoff_reached", diagnostics
    assert rows == [{"status": "open", "bid_count": 1, "expired_at": None}], diagnostics
    assert len(bids) == 1, diagnostics
    assert events == [], diagnostics
    assert bank.escrow_release.await_count == 0, diagnostics


async def test_poster_can_accept_at_selected_cutoff_minus_one_microsecond(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    bid, bank = await _create_task_with_bid(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        acceptance_window_seconds=_DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
    )
    bank.escrow_release.reset_mock()
    acceptance_cutoff = _START + timedelta(
        seconds=_BIDDING_SECONDS + _DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
        microseconds=-1,
    )
    clock.set(acceptance_cutoff)

    response = await _accept_bid(client, poster_keypair[0], bid)
    rows = _read_rows(
        gateway_db_path,
        "SELECT t.status,t.worker_id,t.accepted_bid_id,t.expired_at,c.accepted_reward,"
        "c.accepted_bid_amount,c.timing_contract_version FROM board_tasks AS t "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
        (bid.task.task_id,),
    )
    diagnostics = {
        "response_status": response.status_code,
        "response": response.json(),
        "task_rows": rows,
        "bank_release_calls": bank.escrow_release.await_count,
    }

    assert response.status_code == 200, diagnostics
    assert rows == [
        {
            "status": "accepted",
            "worker_id": bid.worker_id,
            "accepted_bid_id": bid.bid_id,
            "expired_at": None,
            "accepted_reward": 100,
            "accepted_bid_amount": 90,
            "timing_contract_version": 2,
        }
    ], diagnostics
    assert bank.escrow_release.await_count == 0, diagnostics


async def test_task_with_bid_expires_at_selected_acceptance_cutoff_equality(
    client: httpx.AsyncClient,
    gateway_client: httpx.AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _install_domain_clock(monkeypatch)
    bid, bank = await _create_task_with_bid(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
        acceptance_window_seconds=_DEFAULT_ACCEPTANCE_WINDOW_SECONDS,
    )
    bank.escrow_release.reset_mock()
    acceptance_cutoff = _START + timedelta(
        seconds=_BIDDING_SECONDS + _DEFAULT_ACCEPTANCE_WINDOW_SECONDS
    )
    operation_id = _deadline_id(
        bid.task.task_id,
        "expire_acceptance",
        0,
        _epoch_us(acceptance_cutoff),
    )
    _install_persisting_release(
        bank,
        gateway_client,
        gateway_db_path=gateway_db_path,
        task_id=bid.task.task_id,
        operation_id=operation_id,
        command="expire_acceptance",
        cutoff=acceptance_cutoff,
        escrow_id=bid.task.escrow_id,
        recipient_id=bid.task.poster_id,
    )
    clock.set(acceptance_cutoff)

    response = await client.get(f"/tasks/{bid.task.task_id}")
    rows = _read_rows(
        gateway_db_path,
        "SELECT t.status,t.expired_at,c.lifecycle_version,c.timing_contract_version,"
        "c.accepted_reward,c.accepted_bid_amount,c.acceptance_cutoff_us "
        "FROM board_tasks AS t JOIN board_task_contracts AS c ON c.task_id=t.task_id "
        "WHERE t.task_id=?",
        (bid.task.task_id,),
    )
    events = _read_rows(
        gateway_db_path,
        "SELECT event_type FROM events WHERE task_id = ? AND event_type = 'task.expired'",
        (bid.task.task_id,),
    )
    expected_deadline = _iso(acceptance_cutoff)
    diagnostics = {
        "response_status": response.status_code,
        "response": response.json(),
        "task_rows": rows,
        "expiry_events": events,
        "bank_release_calls": bank.escrow_release.await_count,
    }

    assert response.status_code == 200, diagnostics
    assert response.json().get("acceptance_deadline") == expected_deadline, diagnostics
    assert len(rows) == 1 and rows[0]["status"] == "expired", diagnostics
    expired_at = rows[0]["expired_at"]
    assert isinstance(expired_at, str), diagnostics
    assert datetime.fromisoformat(expired_at.replace("Z", "+00:00")) == acceptance_cutoff
    assert rows[0]["lifecycle_version"] == 1, diagnostics
    assert rows[0]["timing_contract_version"] == 2, diagnostics
    assert rows[0]["accepted_reward"] is None, diagnostics
    assert rows[0]["accepted_bid_amount"] is None, diagnostics
    assert rows[0]["acceptance_cutoff_us"] == _epoch_us(acceptance_cutoff), diagnostics
    assert len(events) == 1, diagnostics
    assert bank.escrow_release.await_count == 1, diagnostics
    _assert_refund_completion(
        gateway_db_path,
        task_id=bid.task.task_id,
        poster_id=bid.task.poster_id,
        escrow_id=bid.task.escrow_id,
        operation_id=operation_id,
        command="expire_acceptance",
        cutoff=acceptance_cutoff,
    )
